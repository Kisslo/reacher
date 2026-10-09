"""Bolagsverkets årsredovisningar via API:t för värdefulla datamängder (T1-14, D25).

Per företag:
1. POST /dokumentlista med orgnr i kroppen: digitalt inlämnade årsredovisningar,
   en per räkenskapsår.
2. Av de `years` senaste räkenskapsåren hämtas bara de som inte redan finns i
   financial_fact och som en nyare rapport i samma körning inte redan täckt.
3. GET /dokument/{dokumentId}: en zip med rapporten i xhtml. Den packas upp i
   minnet, läses av parse_annual_report (T1-13) och släpps. Inget skrivs till disk.

Vilka företag som hämtas avgör den som skapar källan (base.py). CLI:t tar dem ur
callable_salon med financial_candidates i ingest.py: SCB filtrerar först (D25).

Vilken rapport gäller (D36): en årsredovisning upprepar förra årets siffror som
jämförelse, och de kan vara omräknade. Den nyaste rapporten som innehåller ett år
vinner, också över ett värde som redan ligger i financial_fact.

Nycklar och token loggas aldrig (D24), och orgnr skickas bara i POST-kroppen,
aldrig i en URL eller en logg. Fel och varningar visar dokument-id.

URL:er, scope och fältnamn kommer från Bolagsverkets utvecklarportal. Ändras API:t,
kontrollera med scripts/bolagsverket/probe.py och ändra konstanterna här.
"""

import base64
import io
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import SecretStr

from reacher.sources.base import RawFinancial
from reacher.sources.ixbrl import ReportFact, ReportParseError, parse_annual_report

log = logging.getLogger(__name__)

TOKEN_URL = "https://portal.api.bolagsverket.se/oauth2/token"
API_URL = "https://gw.api.bolagsverket.se/vardefulla-datamangder/v1"
SCOPE = "vardefulla-datamangder:read"

# Gränsen är 60 anrop i minuten. Ett i sekunden håller oss under den.
MIN_INTERVAL = 1.0
TIMEOUT = 60  # sekunder per anrop
MAX_ATTEMPTS = 4  # första försöket + 3 omförsök
BACKOFF = 2.0  # sekunder före första omförsöket, sedan dubbelt så länge varje gång
MAX_DELAY = 60.0  # längsta väntan, även om Retry-After säger mer
RETRY_STATUSES = {429, 500, 502, 503, 504}
TOKEN_MARGIN = 60  # förnya token en minut innan den går ut
# En årsredovisning är några hundra kB. Större än så är inte en rapport.
MAX_REPORT_BYTES = 50_000_000
REPORT_SUFFIXES = (".xhtml", ".html", ".htm")


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: Mapping[str, str]  # namnen i gemener
    body: bytes


# (metod, url, headers, kropp) -> svar. Testerna skickar in en egen, så inget
# test går mot nätverket.
Send = Callable[[str, str, Mapping[str, str], bytes | None], Response]


def urllib_send(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> Response:
    """Det enda stället som går ut på nätet. Stdlib, inget nytt beroende (som D35)."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
            return Response(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
    except urllib.error.HTTPError as e:
        # 4xx och 5xx är svar, inte nätverksfel: klienten avgör vad som händer.
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        return Response(e.code, headers, e.read())


class BolagsverketError(RuntimeError):
    """Anropet gick inte att genomföra. Innehåller aldrig nycklar, token eller orgnr."""


class BolagsverketAuthError(BolagsverketError):
    """Fel klientuppgifter. Ingen idé att fortsätta med nästa företag."""


@dataclass(frozen=True, slots=True)
class Document:
    """En årsredovisning i dokumentlistan."""

    document_id: str
    period_end: date  # rapporteringsperiodTom, räkenskapsårets slut
    registered_at: str  # registreringstidpunkt, ISO-format, för att välja bland rättelser


class BolagsverketClient:
    """OAuth2 (client credentials), hastighetsgräns och omförsök. Vet inget om databasen."""

    def __init__(
        self,
        client_id: SecretStr,
        client_secret: SecretStr,
        *,
        send: Send | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        min_interval: float = MIN_INTERVAL,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._send = send or urllib_send
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._token: str | None = None  # loggas aldrig
        self._token_expires = 0.0
        self._last_request: float | None = None

    def documents(self, orgnr: str) -> list[Document]:
        """Årsredovisningarna för ett företag. Tom lista om Bolagsverket inte har några."""
        r = self.request("POST", "/dokumentlista", {"identitetsbeteckning": orgnr})
        if r.status == 404:
            return []
        documents = []
        for item in json.loads(r.body).get("dokument") or []:
            try:
                documents.append(
                    Document(
                        document_id=str(item["dokumentId"]),
                        period_end=date.fromisoformat(item["rapporteringsperiodTom"][:10]),
                        registered_at=str(item.get("registreringstidpunkt") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError):
                log.warning("Dokumentlistan har en post utan dokumentId eller period, hoppas över")
        return documents

    def download(self, document_id: str) -> bytes | None:
        """Dokumentet som Bolagsverket levererar det, eller None om det inte finns."""
        r = self.request("GET", "/dokument/" + urllib.parse.quote(document_id, safe=""))
        return None if r.status == 404 else r.body

    def request(self, method: str, path: str, payload: dict | None = None) -> Response:
        """Ett API-anrop med token. Returnerar 200 och 404, kastar för allt annat."""
        body = json.dumps(payload).encode() if payload is not None else None
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._bearer()}", "Accept": "*/*"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            r = self._request(method, API_URL + path, headers, body)
            if r.status == 401 and attempt == 1:
                self._token = None  # token har gått ut i förtid: hämta en ny, en gång
                continue
            if r.status in (200, 404):
                return r
            # Svaret skrivs inte ut: det kan upprepa det vi skickade, alltså orgnr.
            if r.status in (401, 403):
                # Ny token hjälpte inte, eller appen saknar API:t i portalen.
                raise BolagsverketAuthError(f"{method} {path}: HTTP {r.status}")
            raise BolagsverketError(f"{method} {path}: HTTP {r.status}")
        raise AssertionError("nås aldrig: andra varvet returnerar eller kastar alltid")

    def _bearer(self) -> str:
        if self._token is None or self._clock() >= self._token_expires:
            self._fetch_token()
        assert self._token is not None
        return self._token

    def _fetch_token(self) -> None:
        # Det enda stället där nycklarnas värden används (D24).
        pair = f"{self._client_id.get_secret_value()}:{self._client_secret.get_secret_value()}"
        headers = {
            "Authorization": "Basic " + base64.b64encode(pair.encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE})
        r = self._request("POST", TOKEN_URL, headers, body.encode())
        if r.status != 200:
            raise BolagsverketAuthError(
                f"Ingen token från Bolagsverket (HTTP {r.status}). Kontrollera "
                "BOLAGSVERKET_CLIENT_ID och BOLAGSVERKET_CLIENT_SECRET i .env"
            )
        data = json.loads(r.body)
        self._token = data["access_token"]
        self._token_expires = self._clock() + int(data.get("expires_in", 0)) - TOKEN_MARGIN

    def _request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        """Ett anrop med hastighetsgräns och omförsök vid 429, 5xx och nätverksfel."""
        problem = ""
        for attempt in range(1, self._max_attempts + 1):
            self._wait_for_turn()
            retry_after = None
            try:
                r = self._send(method, url, headers, body)
            except OSError as e:  # nätverksfel och timeout
                reason = getattr(e, "reason", e)  # URLError lindar in det verkliga felet
                if isinstance(reason, ssl.SSLCertVerificationError):
                    # Blir inte bättre av att vänta. Stäng aldrig av verifieringen: se README.
                    detail = getattr(reason, "verify_message", None) or reason
                    raise BolagsverketError(
                        f"TLS-certifikatet kunde inte verifieras: {detail}"
                    ) from None
                problem = type(reason).__name__
            else:
                if r.status not in RETRY_STATUSES:
                    return r
                problem = f"HTTP {r.status}"
                retry_after = _retry_after(r)
            if attempt == self._max_attempts:
                break
            delay = min(retry_after or BACKOFF * 2 ** (attempt - 1), MAX_DELAY)
            log.warning(
                "Bolagsverket: %s, försök %d av %d, väntar %.0f s",
                problem,
                attempt,
                self._max_attempts,
                delay,
            )
            self._sleep(delay)
        raise BolagsverketError(f"Bolagsverket svarar inte: {problem} efter {attempt} försök")

    def _wait_for_turn(self) -> None:
        if self._last_request is not None:
            wait = self._last_request + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()


def _retry_after(r: Response) -> float | None:
    """Retry-After i sekunder. Ett datum i stället ignoreras, då gäller backoff."""
    try:
        return max(float(r.headers.get("retry-after", "")), 0.0)
    except ValueError:
        return None


def report_files(payload: bytes, document_id: str) -> list[bytes]:
    """xhtml-filerna i ett dokument. Bolagsverket levererar en zip; ren xhtml går också."""
    if not zipfile.is_zipfile(io.BytesIO(payload)):
        return [payload]  # parse_annual_report säger till om det inte är iXBRL
    files = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            if not member.filename.lower().endswith(REPORT_SUFFIXES):
                continue
            # file_size är den uppackade storleken, och zipfile läser aldrig mer än
            # så. Stoppar en zip-bomb innan den packas upp.
            if member.file_size > MAX_REPORT_BYTES:
                log.warning(
                    "Dokument %s: %s är för stor, hoppas över", document_id, member.filename
                )
                continue
            files.append(archive.read(member))
    if not files:
        log.warning("Dokument %s: ingen xhtml-fil i zip-filen", document_id)
    return files


def newest_first(documents: Iterable[Document]) -> list[Document]:
    """En rapport per räkenskapsår, nyaste året först. Har ett år lämnats in mer än en
    gång (en rättelse) gäller den som registrerades sist."""
    latest: dict[date, Document] = {}
    for doc in sorted(documents, key=lambda d: d.registered_at):
        latest[doc.period_end] = doc
    return sorted(latest.values(), key=lambda d: d.period_end, reverse=True)


class BolagsverketSource:
    """FinancialSource för Bolagsverket. Samma form som CsvFinancialSource (base.py)."""

    name = "bolagsverket"

    def __init__(
        self,
        client: BolagsverketClient,
        orgnrs: Iterable[str],
        tag_map: Mapping[str, str],
        years: int,
        stored_years: Mapping[str, set[date]],
    ) -> None:
        self.client = client
        self.orgnrs = list(orgnrs)
        self.tag_map = dict(tag_map)
        self.years = years
        # Räkenskapsår per orgnr som redan finns i financial_fact. Hämtas aldrig igen.
        self.stored_years = stored_years

    def fetch(self) -> Iterator[RawFinancial]:
        for n, orgnr in enumerate(self.orgnrs, start=1):
            yield from self.fetch_company(orgnr, n)

    def fetch_company(self, orgnr: str, n: int = 0) -> Iterator[RawFinancial]:
        """Värdena för ett företag. n är företagets nummer i körningen, för loggen."""
        documents = newest_first(self.client.documents(orgnr))[: self.years]
        if not documents:
            log.info("Företag %d: inga digitala årsredovisningar", n)
            return
        # Ungefär `years` år bakåt från det senaste räkenskapsåret. Äldre år i
        # flerårsöversikten sparas inte (D25).
        oldest = documents[0].period_end - timedelta(days=365 * self.years)
        stored = self.stored_years.get(orgnr, set())
        all_keys = set(self.tag_map.values())

        found: dict[tuple[date, str], RawFinancial] = {}
        for doc in documents:  # nyaste först, så första värdet för ett år vinner (D36)
            covered = {key for (period_end, key) in found if period_end == doc.period_end}
            if doc.period_end in stored or covered >= all_keys:
                continue
            for fact in self._facts(doc):
                if fact.period_end <= oldest:
                    continue
                found.setdefault(
                    (fact.period_end, fact.key),
                    RawFinancial(orgnr, fact.period_end, fact.key, fact.value, doc.document_id),
                )
        yield from found.values()

    def _facts(self, doc: Document) -> list[ReportFact]:
        payload = self.client.download(doc.document_id)
        if payload is None:
            log.warning("Dokument %s: finns inte hos Bolagsverket, hoppas över", doc.document_id)
            return []
        facts: list[ReportFact] = []
        for xhtml in report_files(payload, doc.document_id):
            try:
                facts += parse_annual_report(xhtml, self.tag_map, doc.document_id)
            except ReportParseError as e:
                log.warning("%s, hoppas över", e)  # meddelandet har bara dokument-id
        return facts
