"""T1-14: Bolagsverket-adaptern. Inget nätverk: alla svar är påhittade.

dokumentlista.json har samma form som Bolagsverkets svar (kontrollerad med
scripts/bolagsverket/probe.py). Den senaste rapporten är arsredovisning_fiktiv.xhtml
från T1-13, de äldre byggs som små rapporter här i filen.
"""

import base64
import io
import json
import logging
import ssl
import urllib.error
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from reacher.cli import app
from reacher.db import connect, migrate
from reacher.ingest import financial_candidates, ingest, stored_fiscal_years
from reacher.sources import bolagsverket
from reacher.sources.base import FinancialSource, RawFinancial
from reacher.sources.bolagsverket import (
    API_URL,
    TOKEN_URL,
    BolagsverketAuthError,
    BolagsverketClient,
    BolagsverketError,
    BolagsverketSource,
    Response,
    report_files,
)
from reacher.sources.config import (
    BOLAGSVERKET_CLIENT_ID,
    BOLAGSVERKET_CLIENT_SECRET,
    SourcesConfig,
)
from reacher.sources.csv_source import CsvSource

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "bolagsverket" / "arsredovisning_fiktiv.xhtml"
DOCUMENT_LIST = json.loads((FIXTURES / "bolagsverket" / "dokumentlista.json").read_text("utf-8"))
TAG_MAP = SourcesConfig.load(REPO_ROOT / "sources.yaml").bolagsverket.tag_map

ORGNR = "5599990016"  # Fiktiva Saxen AB, samma som i rapporten
KAMGARDEN = "5560009697"  # AB i salons.csv utan rader i financials.csv
# Påhittade. Ser ut som nycklar så att ett läckage syns i assert-meddelandet.
CLIENT_ID = "test-client-id-0000"
CLIENT_SECRET = "test-client-secret-0000"
TOKEN = "fiktiv-token-0000"

FY26, FY25, FY24 = date(2026, 4, 30), date(2025, 4, 30), date(2024, 4, 30)


# --- Påhittat Bolagsverket ---------------------------------------------------


def small_report(period_end: date, revenue: int, net_result: int) -> bytes:
    """En minimal årsredovisning med ett räkenskapsår."""

    def fact(name: str, value: int) -> str:
        sign = ' sign="-"' if value < 0 else ""
        return (
            f'<ix:nonFraction name="se-gen-base:{name}" contextRef="p" unitRef="SEK" '
            f'scale="0" decimals="0"{sign}>{abs(value)}</ix:nonFraction>'
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:se-gen-base="http://www.taxonomier.se/se/fr/gen-base/2021-10-31">
<head><title>Fiktiv</title></head>
<body>
<ix:header><ix:resources>
  <xbrli:context id="p">
    <xbrli:entity><xbrli:identifier scheme="http://www.bolagsverket.se">559999-0016</xbrli:identifier></xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>{period_end.replace(year=period_end.year - 1)}</xbrli:startDate>
      <xbrli:endDate>{period_end}</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>
  <xbrli:unit id="SEK"><xbrli:measure>iso4217:SEK</xbrli:measure></xbrli:unit>
</ix:resources></ix:header>
<p>{fact("Nettoomsattning", revenue)}</p>
<p>{fact("AretsResultat", net_result)}</p>
</body>
</html>""".encode()


def zipped(**files: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name.replace("_", "."), content)
    return buffer.getvalue()


REPORTS = {
    "FIKTIV-T114-2026": SAMPLE.read_bytes(),
    # Omsättningen 2024 skiljer sig från jämförelsetalet i 2026 års rapport (4 105 220).
    "FIKTIV-T114-2024": small_report(FY24, 4_100_000, 51_000),
    "FIKTIV-T114-2025": small_report(FY25, 4_388_615, -48_210),
    "FIKTIV-T114-2023": small_report(date(2023, 4, 30), 3_912_040, 22_000),
}


def ok(data) -> Response:
    return Response(200, {}, json.dumps(data).encode())


TOKEN_RESPONSE = ok({"access_token": TOKEN, "token_type": "Bearer", "expires_in": 3600})
NO_DOCUMENTS = ok({"dokument": []})


@dataclass
class Call:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


class FakeApi:
    """Bolagsverket i minnet: token, dokumentlista per orgnr och dokument som zip."""

    def __init__(self, documents: dict | None = None, reports: dict | None = None) -> None:
        self.documents = documents if documents is not None else {ORGNR: DOCUMENT_LIST}
        self.reports = reports if reports is not None else REPORTS
        self.calls: list[Call] = []

    @property
    def downloads(self) -> list[str]:
        return [c.url.rsplit("/", 1)[1] for c in self.calls if "/dokument/" in c.url]

    def __call__(self, method, url, headers, body) -> Response:
        self.calls.append(Call(method, url, dict(headers), body))
        if url == TOKEN_URL:
            return TOKEN_RESPONSE
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        path = url.removeprefix(API_URL)
        if path == "/dokumentlista":
            orgnr = json.loads(body)["identitetsbeteckning"]
            return ok(self.documents.get(orgnr, {"dokument": []}))
        document_id = path.removeprefix("/dokument/")
        if document_id in self.reports:
            return Response(200, {}, zipped(arsredovisning_xhtml=self.reports[document_id]))
        return Response(404, {}, b"")


class Scripted:
    """Svarar med svaren i tur och ordning. Ett undantag i listan kastas."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[Call] = []

    def __call__(self, method, url, headers, body) -> Response:
        self.calls.append(Call(method, url, dict(headers), body))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClock:
    """time.monotonic och time.sleep utan att vänta på riktigt."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def client(send, clock=None, **kwargs) -> BolagsverketClient:
    clock = clock or FakeClock()
    return BolagsverketClient(
        SecretStr(CLIENT_ID),
        SecretStr(CLIENT_SECRET),
        send=send,
        sleep=clock.sleep,
        clock=clock,
        **kwargs,
    )


def source(api, stored=None, years=3) -> BolagsverketSource:
    return BolagsverketSource(client(api), [ORGNR], TAG_MAP, years, stored or {})


def facts(api, stored=None) -> set[tuple[date, str, int, str]]:
    return {(f.period_end, f.key, f.value, f.source_document) for f in source(api, stored).fetch()}


# --- Kandidater: SCB filtrerar först (D25) ------------------------------------


@pytest.fixture
def conn(tmp_path):
    with closing(connect(tmp_path / "t.db")) as c:
        migrate(c)
        ingest(c, CsvSource(FIXTURES).fetch(), now="2026-10-09T08:00:00+00:00")
        yield c


def test_candidates_are_callable_companies_with_annual_reports(conn):
    candidates = financial_candidates(conn, ["49"])
    assert len(candidates) == 30
    assert candidates == sorted(set(candidates))  # ett orgnr en gång, i ordning
    assert "5590001011" in candidates  # Kedjan Klipp: flera salonger, en rapport
    assert "9690006011" not in candidates  # HB: inte i annual_report_legal_forms
    assert "8503122148" not in candidates  # enskild firma: lämnar ingen årsredovisning
    assert "5560007006" not in candidates  # AB med telefonspärr: inte ringbar


def test_suppression_after_ingest_removes_the_candidate(conn):
    """Spärren läses när hämtningen körs, inte när salongen lästes in."""
    conn.execute(
        "INSERT INTO suppression (orgnr, reason, created_at) VALUES (?, 'opt_out', ?)",
        (KAMGARDEN, "2026-10-09T09:00:00+00:00"),
    )
    assert KAMGARDEN not in financial_candidates(conn, ["49"])


def test_stored_fiscal_years(conn):
    conn.execute(
        "INSERT INTO financial_fact (orgnr, period_end, key, value, fetched_at) "
        "VALUES (?, '2025-04-30', 'revenue', 1, 'x'), (?, '2025-04-30', 'net_result', 1, 'x')",
        (ORGNR, ORGNR),
    )
    assert stored_fiscal_years(conn) == {ORGNR: {FY25}}


# --- Vad som hämtas ----------------------------------------------------------


def test_source_satisfies_the_protocol():
    assert isinstance(source(FakeApi()), FinancialSource)


def test_first_run_gets_three_years_and_newest_report_wins():
    """2026 års rapport täcker 2025 helt (hämtas inte) men 2024 bara med omsättning,
    så 2024 års rapport hämtas för resultatet. 2023 ligger utanför de 3 åren."""
    api = FakeApi()
    assert facts(api) == {
        (FY26, "revenue", 4_702_330, "FIKTIV-T114-2026"),
        (FY26, "net_result", 126_455, "FIKTIV-T114-2026"),
        (FY25, "revenue", 4_388_615, "FIKTIV-T114-2026"),
        (FY25, "net_result", -48_210, "FIKTIV-T114-2026"),
        (FY24, "revenue", 4_105_220, "FIKTIV-T114-2026"),  # nyaste rapporten vinner (D36)
        (FY24, "net_result", 51_000, "FIKTIV-T114-2024"),
    }
    assert api.downloads == ["FIKTIV-T114-2026", "FIKTIV-T114-2024"]


def test_years_already_stored_are_not_downloaded_again():
    api = FakeApi()
    assert facts(api, stored={ORGNR: {FY26, FY25, FY24}}) == set()
    assert api.downloads == []


def test_a_new_report_is_downloaded_and_its_comparison_years_win():
    """Nästa år: bara den nya rapporten hämtas. Dess jämförelsetal ersätter de lagrade."""
    api = FakeApi()
    got = facts(api, stored={ORGNR: {FY25, FY24}})
    assert api.downloads == ["FIKTIV-T114-2026"]
    assert {(pe, key) for pe, key, _, _ in got} == {
        (FY26, "revenue"),
        (FY26, "net_result"),
        (FY25, "revenue"),
        (FY25, "net_result"),
        (FY24, "revenue"),
    }


def test_years_setting_limits_the_downloads():
    api = FakeApi()
    list(source(api, years=1).fetch())
    assert api.downloads == ["FIKTIV-T114-2026"]


def test_company_without_reports_gives_no_rows():
    api = FakeApi(documents={})
    assert facts(api) == set()
    assert api.downloads == []


def test_corrected_report_replaces_the_first_one():
    """Två inlämningar för samma år: den som registrerades sist gäller."""
    corrected = {
        "dokumentId": "FIKTIV-T114-2026-R",
        "rapporteringsperiodTom": "2026-04-30",
        "registreringstidpunkt": "2026-10-01",
    }
    documents = {"dokument": [*DOCUMENT_LIST["dokument"], corrected]}
    api = FakeApi(documents={ORGNR: documents}, reports={"FIKTIV-T114-2026-R": SAMPLE.read_bytes()})
    list(source(api, stored={ORGNR: {FY25, FY24}}).fetch())
    assert api.downloads == ["FIKTIV-T114-2026-R"]


def test_missing_or_broken_document_is_skipped_with_a_warning(caplog):
    api = FakeApi(reports={"FIKTIV-T114-2024": b"<html>inte iXBRL</html>"})
    got = facts(api)
    assert got == set()  # 2026 finns inte (404), 2025 och 2024 går inte att läsa
    assert "FIKTIV-T114-2026: finns inte" in caplog.text
    assert "FIKTIV-T114-2024" in caplog.text


def test_orgnr_is_only_sent_in_the_body_and_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    api = FakeApi(reports={})  # 404 på alla dokument ger varningar att leta i
    list(source(api).fetch())
    assert all(ORGNR not in c.url for c in api.calls)
    for orgnr in (ORGNR, "559999-0016"):
        assert orgnr not in caplog.text


# --- Dokumentet ----------------------------------------------------------------


def test_zip_is_unpacked_in_memory():
    payload = zipped(arsredovisning_xhtml=b"<html/>", meta_json=b"{}")
    assert report_files(payload, "D1") == [b"<html/>"]


def test_plain_xhtml_is_passed_through():
    assert report_files(b"<html/>", "D1") == [b"<html/>"]


def test_zip_without_xhtml_gives_a_warning(caplog):
    assert report_files(zipped(arsredovisning_pdf=b"%PDF"), "D1") == []
    assert "Dokument D1: ingen xhtml-fil" in caplog.text


# --- Klienten: token, hastighetsgräns, omförsök --------------------------------


def test_token_uses_basic_auth_and_the_api_gets_a_bearer_token():
    api = Scripted(TOKEN_RESPONSE, NO_DOCUMENTS)
    assert client(api).documents(ORGNR) == []

    token_call, list_call = api.calls
    assert (token_call.method, token_call.url) == ("POST", TOKEN_URL)
    pair = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert token_call.headers["Authorization"] == f"Basic {pair}"
    assert token_call.body == b"grant_type=client_credentials&scope=vardefulla-datamangder%3Aread"

    assert (list_call.method, list_call.url) == ("POST", f"{API_URL}/dokumentlista")
    assert list_call.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(list_call.body) == {"identitetsbeteckning": ORGNR}


def test_token_is_reused_until_it_is_about_to_expire():
    clock = FakeClock()
    api = Scripted(TOKEN_RESPONSE, NO_DOCUMENTS, NO_DOCUMENTS, TOKEN_RESPONSE, NO_DOCUMENTS)
    c = client(api, clock, min_interval=0)
    c.documents(ORGNR)
    c.documents(ORGNR)
    clock.now += 3600 - 60  # en minut kvar: förnya
    c.documents(ORGNR)
    assert [call.url for call in api.calls].count(TOKEN_URL) == 2


def test_401_gets_a_new_token_once():
    unauthorized = Response(401, {}, b"")
    api = Scripted(TOKEN_RESPONSE, unauthorized, TOKEN_RESPONSE, NO_DOCUMENTS)
    assert client(api).documents(ORGNR) == []

    api = Scripted(TOKEN_RESPONSE, unauthorized, TOKEN_RESPONSE, unauthorized)
    with pytest.raises(BolagsverketAuthError):
        client(api).documents(ORGNR)


def test_wrong_credentials_name_the_variables_but_never_show_them(caplog):
    api = Scripted(Response(401, {}, f'{{"client_id": "{CLIENT_ID}"}}'.encode()))
    with pytest.raises(BolagsverketAuthError) as error:
        client(api).documents(ORGNR)
    assert BOLAGSVERKET_CLIENT_SECRET in str(error.value)
    for secret in (CLIENT_ID, CLIENT_SECRET):
        assert secret not in str(error.value)
        assert secret not in caplog.text


def test_server_errors_are_retried_with_backoff():
    clock = FakeClock()
    api = Scripted(TOKEN_RESPONSE, Response(503, {}, b""), Response(502, {}, b""), NO_DOCUMENTS)
    assert client(api, clock, min_interval=0).documents(ORGNR) == []
    assert clock.sleeps == [2.0, 4.0]


def test_retry_after_is_respected():
    clock = FakeClock()
    api = Scripted(TOKEN_RESPONSE, Response(429, {"retry-after": "7"}, b""), NO_DOCUMENTS)
    client(api, clock, min_interval=0).documents(ORGNR)
    assert clock.sleeps == [7.0]


def test_network_errors_are_retried():
    clock = FakeClock()
    api = Scripted(TOKEN_RESPONSE, TimeoutError(), ConnectionResetError(), NO_DOCUMENTS)
    assert client(api, clock, min_interval=0).documents(ORGNR) == []
    assert clock.sleeps == [2.0, 4.0]


def test_certificate_error_is_not_retried():
    """Ett certifikatfel blir inte bättre av att vänta, och verifieringen stängs aldrig av."""
    clock = FakeClock()
    bad_cert = urllib.error.URLError(ssl.SSLCertVerificationError(1, "verify failed"))
    with pytest.raises(BolagsverketError, match="TLS-certifikatet kunde inte verifieras"):
        client(Scripted(bad_cert), clock, min_interval=0).documents(ORGNR)
    assert clock.sleeps == []


def test_gives_up_after_the_last_attempt(caplog):
    clock = FakeClock()
    api = Scripted(TOKEN_RESPONSE, *[Response(503, {}, b"")] * 4)
    with pytest.raises(BolagsverketError, match="HTTP 503 efter 4 försök"):
        client(api, clock, min_interval=0).documents(ORGNR)
    assert clock.sleeps == [2.0, 4.0, 8.0]
    assert TOKEN not in caplog.text


def test_client_errors_are_not_retried():
    api = Scripted(TOKEN_RESPONSE, Response(400, {}, f'{{"fel": "{ORGNR}"}}'.encode()))
    with pytest.raises(BolagsverketError, match="HTTP 400") as error:
        client(api).documents(ORGNR)
    assert ORGNR not in str(error.value)  # svarskroppen skrivs aldrig ut
    assert len(api.calls) == 2


def test_requests_are_spaced_to_respect_the_rate_limit():
    """60 anrop i minuten: minst en sekund mellan två anrop."""
    clock = FakeClock()
    api = Scripted(TOKEN_RESPONSE, NO_DOCUMENTS, NO_DOCUMENTS)
    c = client(api, clock)
    c.documents(ORGNR)
    c.documents(ORGNR)
    assert clock.sleeps == [1.0, 1.0]


def test_unknown_company_and_missing_document_are_not_errors():
    api = Scripted(TOKEN_RESPONSE, Response(404, {}, b""), Response(404, {}, b""))
    c = client(api)
    assert c.documents(ORGNR) == []
    assert c.download("FIKTIV-X") is None


# --- CLI ---------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv(BOLAGSVERKET_CLIENT_ID, CLIENT_ID)
    monkeypatch.setenv(BOLAGSVERKET_CLIENT_SECRET, CLIENT_SECRET)


def fetch(db: Path):
    config = str(REPO_ROOT / "sources.yaml")
    return runner.invoke(app, ["fetch-financials", "--db", str(db), "--config", config])


def test_fetch_financials_needs_the_keys(monkeypatch, tmp_path):
    monkeypatch.delenv(BOLAGSVERKET_CLIENT_ID, raising=False)
    result = fetch(tmp_path / "t.db")
    assert result.exit_code == 1
    assert f"{BOLAGSVERKET_CLIENT_ID} saknas" in result.output


def test_fetch_financials_end_to_end_and_again(keys, monkeypatch, tmp_path):
    """load-seed, sedan två hämtningar. Den andra laddar inte ner något."""
    api = FakeApi(documents={KAMGARDEN: DOCUMENT_LIST})
    monkeypatch.setattr(bolagsverket, "urllib_send", api)
    monkeypatch.setattr(bolagsverket.time, "sleep", lambda seconds: None)
    db = tmp_path / "cli.db"
    assert runner.invoke(app, ["load-seed", "--db", str(db)]).exit_code == 0

    first = fetch(db)
    assert first.exit_code == 0, first.output
    assert "30 företag, 0 hoppades över. 6 nya, 0 ändrade" in first.output
    assert api.downloads == ["FIKTIV-T114-2026", "FIKTIV-T114-2024"]

    second = fetch(db)
    assert "0 nya, 0 ändrade" in second.output
    assert len(api.downloads) == 2  # inget nytt

    for shown in (first.output, second.output):
        assert CLIENT_SECRET not in shown and KAMGARDEN not in shown
    with closing(connect(db)) as conn:
        rows = conn.execute(
            "SELECT period_end, key, value FROM latest_financial_fact "
            "WHERE orgnr = ? AND fiscal_year_rank = 1 ORDER BY key",
            (KAMGARDEN,),
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("2026-04-30", "net_result", 126_455),
        ("2026-04-30", "revenue", 4_702_330),
    ]


def test_fetch_financials_stops_when_bolagsverket_is_down(keys, monkeypatch, tmp_path):
    def down(method, url, headers, body):
        if url == TOKEN_URL:
            return TOKEN_RESPONSE
        return Response(503, {}, b"")

    monkeypatch.setattr(bolagsverket, "urllib_send", down)
    monkeypatch.setattr(bolagsverket.time, "sleep", lambda seconds: None)
    db = tmp_path / "cli.db"
    runner.invoke(app, ["load-seed", "--db", str(db)])
    result = fetch(db)
    assert result.exit_code == 1
    assert "3 företag i rad misslyckades, avbryter" in result.output


def test_raw_financial_carries_the_document_id():
    first = next(iter(source(FakeApi()).fetch()))
    assert isinstance(first, RawFinancial)
    assert first.orgnr == ORGNR and first.source_document == "FIKTIV-T114-2026"
