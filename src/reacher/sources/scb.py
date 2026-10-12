"""SCB:s företagsregister via det nya API:t med nyckel (T1-06, D24).

Fält, koder och endpoints: docs/scb-fields.md (T1-09). Per körning:
1. GET /arbetsstallen/naringsgren/{sni} för varje SNI-kod i sources.yaml, med
   cursor. API:t kan inte filtrera på SNI och kommun i samma anrop.
2. Arbetsställen vars belagenhetsadress.kommun finns i sources.yaml behålls.
   Aldrig kommunSate: för en enskild firma är det ägarens hem (D19).
3. GET /arbetsstallen/{cfarNr}/full för varje behållet arbetsställe (telefon).
4. GET /juridiskaenheter/{peOrgNr}/full en gång per företag och körning.

Källan levererar data som SCB gör (base.py), med de två undantag som
docs/scb-fields.md beslutar: koder blir text med str() och tolkas aldrig (D18), och
tomma strängar blir None, så att NULL fortsätter betyda "källan sa ingenting".

För en enskild firma är peOrgNr ett personnummer. Det står i URL:en eftersom API:t
kräver det, men loggas aldrig och står aldrig i ett felmeddelande. Fel visar
endpointens mall, t.ex. /juridiskaenheter/{peOrgNr}/full. Nyckeln skickas bara i
headern X-API-Key (D24).
"""

import json
import logging
import ssl
import time
import urllib.parse
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from reacher.sources.base import RawSalon

# Samma HTTP-lager som Bolagsverket-adaptern: stdlib urllib, inget nytt beroende (D36).
from reacher.sources.bolagsverket import Response, Send, urllib_send

log = logging.getLogger(__name__)

API_URL = "https://apiafr.scb.se/v1"
PAGE_LIMIT = 5000  # API:ts största sida
# Anropsgränsen är inte dokumenterad. Fyra i sekunden är ett antagande: ger SCB
# 429 väntar klienten och försöker igen.
MIN_INTERVAL = 0.25
MAX_ATTEMPTS = 4  # första försöket + 3 omförsök
BACKOFF = 2.0  # sekunder före första omförsöket, sedan dubbelt så länge varje gång
MAX_DELAY = 60.0
RETRY_STATUSES = {429, 500, 502, 503, 504}

# SCB skickar datum som UTC. Ett datum utan klockslag blir svensk midnatt, alltså
# 23:00 eller 22:00 dagen innan i UTC (docs/scb-fields.md).
STOCKHOLM = ZoneInfo("Europe/Stockholm")


class ScbError(RuntimeError):
    """Anropet gick inte att genomföra. Innehåller aldrig nyckel eller orgnr."""


class ScbAuthError(ScbError):
    """Fel eller saknad nyckel. Ingen idé att försöka igen."""


class ScbClient:
    """Nyckel, hastighetsgräns och omförsök. Vet inget om salonger eller databasen."""

    def __init__(
        self,
        key: SecretStr,
        *,
        send: Send | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        min_interval: float = MIN_INTERVAL,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._key = key
        self._send = send or urllib_send
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._last_request: float | None = None

    def workplaces_page(self, sni: str, limit: int, cursor: int | None) -> dict:
        """En sida arbetsställen med primär näringsgren `sni`."""
        params: dict[str, int] = {"limit": limit}
        if cursor is not None:
            params["cursorId"] = cursor
        path = f"/arbetsstallen/naringsgren/{urllib.parse.quote(sni, safe='')}"
        data = self.get(path, params, "/arbetsstallen/naringsgren/{kod}")
        if data is None:
            raise ScbError(f"SNI-koden {sni} finns inte hos SCB")  # en kod är inte personlig
        return data

    def workplace(self, cfar: str) -> dict | None:
        """Ett arbetsställe med alla variabler, eller None om SCB inte har det."""
        path = f"/arbetsstallen/{urllib.parse.quote(cfar, safe='')}/full"
        return self.get(path, {}, "/arbetsstallen/{cfarNr}/full")

    def company(self, pe_orgnr: str) -> dict | None:
        """En juridisk enhet med alla variabler, eller None om SCB inte har den."""
        path = f"/juridiskaenheter/{urllib.parse.quote(pe_orgnr, safe='')}/full"
        return self.get(path, {}, "/juridiskaenheter/{peOrgNr}/full")

    def get(self, path: str, params: dict[str, int], label: str) -> dict | None:
        """JSON för 200, None för 404, kastar för allt annat. `label` är det enda
        som visas i fel och loggar: path kan innehålla ett personnummer."""
        url = API_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
        # Det enda stället där nyckelns värde används (D24).
        headers = {"X-API-Key": self._key.get_secret_value(), "Accept": "application/json"}
        r = self._request(url, headers, label)
        if r.status == 200:
            return json.loads(r.body)
        if r.status == 404:
            return None
        # Svaret skrivs inte ut: problem+json kan upprepa sökvägen, alltså orgnr.
        if r.status in (401, 403):
            raise ScbAuthError(f"GET {label}: HTTP {r.status}. Kontrollera SCB_API_KEY i .env")
        raise ScbError(f"GET {label}: HTTP {r.status}")

    def _request(self, url: str, headers: dict[str, str], label: str) -> Response:
        """Ett anrop med hastighetsgräns och omförsök vid 429, 5xx och nätverksfel."""
        problem = ""
        for attempt in range(1, self._max_attempts + 1):
            self._wait_for_turn()
            retry_after = None
            try:
                r = self._send("GET", url, headers, None)
            except OSError as e:  # nätverksfel och timeout
                reason = getattr(e, "reason", e)  # URLError lindar in det verkliga felet
                if isinstance(reason, ssl.SSLCertVerificationError):
                    # Blir inte bättre av att vänta. Stäng aldrig av verifieringen.
                    detail = getattr(reason, "verify_message", None) or reason
                    raise ScbError(f"TLS-certifikatet kunde inte verifieras: {detail}") from None
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
                "SCB %s: %s, försök %d av %d, väntar %.0f s",
                label,
                problem,
                attempt,
                self._max_attempts,
                delay,
            )
            self._sleep(delay)
        raise ScbError(f"SCB svarar inte på {label}: {problem} efter {attempt} försök")

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


def in_maintenance_window(moment: datetime) -> bool:
    """SCB laddar data 04:00-04:30 svensk tid och svarar inte då."""
    local = moment.astimezone(STOCKHOLM)
    return local.hour == 4 and local.minute < 30


# --- Från SCB:s JSON till RawSalon ------------------------------------------


def _text(value: object) -> str | None:
    """Fritext (namn, adress, telefon). SCB skickar "" eller " " när värdet saknas."""
    if value is None:
        return None
    return str(value).strip() or None


def _code(value: object) -> str | None:
    """En kod som text med str() och inget annat (D18). 1 och "1" blir båda "1".
    None och tom sträng betyder att SCB inte sa något. str(None) vore "None"."""
    if value is None or str(value).strip() == "":
        return None
    return str(value)


def _date(value: object) -> date | None:
    """2025-02-02T23:00:00.000Z -> 2025-02-03: datumet i Sverige, inte i UTC."""
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text).astimezone(STOCKHOLM).date()
    except ValueError:
        log.warning("SCB: datum i okänt format, sparas inte")  # aldrig värdet
        return None


def to_raw_salon(ae: dict, ae_full: dict | None, je: dict | None) -> RawSalon:
    """Ett arbetsställe som RawSalon, enligt fältmappningen i docs/scb-fields.md.

    ae är posten ur listanropet (partial). ae_full och je är None om SCB svarade 404:
    då blir fälten därifrån None, och callable_salon utesluter salongen (fail closed).
    """
    ae_full = ae_full or {}
    je = je or {}
    address = ae.get("belagenhetsadress") or {}  # besöksadressen, aldrig postAdress (D19)
    industry = ae.get("primarNaringsgren") or {}
    # ben (salongens namn), annars registrerat företagsnamn, annars juridiskt namn.
    # Tom sträng blir ingest:s "saknar namn", och salongen avvisas.
    name = _text(ae.get("ben")) or _text(je.get("foretagsnamn")) or _text(je.get("namn")) or ""
    return RawSalon(
        orgnr=_text(ae.get("peOrgNr")) or "",  # 12 siffror, normaliseras vid ingest
        name=name,
        cfar=_code(ae.get("cfarNr")),  # ett tal i JSON, text i databasen
        sni=_code(industry.get("naringsgren")),
        street=_text(address.get("bGatuAdress")),
        postal_code=_text(address.get("bPostNr")),
        city=_text(address.get("bPostOrt")),
        municipality=_code(address.get("kommun")),
        employee_class=_code(ae.get("anstKl")),  # arbetsställets, inte företagets
        registered_at=_date(je.get("regDat")),
        website=None,  # finns inte i nya API:t (docs/scb-fields.md, "Flaggat")
        # Arbetsställets nummer först, annars företagets.
        phone=_text(ae_full.get("tel")) or _text(je.get("tel")),
        legal_form=_code(je.get("jurform")),
        ftax_status=_code(je.get("fSkattStat")),
        vat_status=_code(je.get("momsStat")),
        employer_status=_code(je.get("arbGivStat")),
        company_status=_code(je.get("ftgStat")),
        workplace_status=_code(ae.get("aeStat")),
        ad_block_type=_code(je.get("reklamSparrTyp")),
        phone_block_type=_code(je.get("telefonSparrTyp")),
        workplace_ad_block_type=_code(ae.get("reklamSparrTyp")),
        workplace_phone_block_type=_code(ae.get("telefonSparrTyp")),
    )


@dataclass
class ScbStats:
    """Antal, aldrig vem. Skrivs ut efter körningen."""

    listed: int = 0  # arbetsställen i SNI-listorna, hela landet
    kept: int = 0  # i de konfigurerade kommunerna
    companies: int = 0  # unika företag som hämtades
    with_phone: int = 0  # behållna arbetsställen med ett telefonnummer från SCB


class ScbApiSource:
    """SalonSource för SCB:s nya API. Yield:ar en salong i taget (lat)."""

    name = "scb"

    def __init__(
        self,
        client: ScbClient,
        sni_codes: Iterable[str],
        municipalities: Iterable[str],
        page_limit: int = PAGE_LIMIT,
    ) -> None:
        self.client = client
        self.sni_codes = list(sni_codes)
        self.municipalities = set(municipalities)
        self.page_limit = page_limit
        self.stats = ScbStats()

    def fetch(self) -> Iterator[RawSalon]:
        self.stats = ScbStats()
        companies: dict[str, dict | None] = {}  # cache per körning: ett företag, ett anrop
        seen: set[str] = set()
        for sni in self.sni_codes:
            for ae in self._workplaces(sni):
                self.stats.listed += 1
                address = ae.get("belagenhetsadress") or {}
                if _code(address.get("kommun")) not in self.municipalities:
                    continue
                cfar = _code(ae.get("cfarNr"))
                if cfar is not None:
                    if cfar in seen:
                        continue
                    seen.add(cfar)
                self.stats.kept += 1

                ae_full = self.client.workplace(cfar) if cfar is not None else None
                pe_orgnr = _text(ae.get("peOrgNr"))
                if pe_orgnr is not None and pe_orgnr not in companies:
                    companies[pe_orgnr] = self.client.company(pe_orgnr)
                    self.stats.companies += 1
                je = companies.get(pe_orgnr) if pe_orgnr is not None else None

                salon = to_raw_salon(ae, ae_full, je)
                if salon.phone:
                    self.stats.with_phone += 1
                yield salon

    def _workplaces(self, sni: str) -> Iterator[dict]:
        """Alla arbetsställen för en SNI-kod. Slutet avgörs bara av hasMore, och
        samma limit skickas på varje sida (docs/scb-fields.md, "Paginering")."""
        cursor: int | None = None
        while True:
            page = self.client.workplaces_page(sni, self.page_limit, cursor)
            yield from page.get("arbetsstallen") or []
            pagination = page["pagination"]
            if not pagination["hasMore"]:
                return
            if pagination["nextCursorId"] == cursor:
                # Samma cursor igen skulle ge en oändlig loop.
                raise ScbError(f"SNI {sni}: SCB gav samma cursor två gånger")
            cursor = pagination["nextCursorId"]
