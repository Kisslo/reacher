"""T1-06: SCB-adaptern. Inget nätverk: alla svar är påhittade.

Den enskilda firman kommer från de inspelade svaren i tests/fixtures/scb/ (T1-09),
som har exakt samma form som SCB:s. Aktiebolaget byggs här i filen av samma svar.
"""

import json
import logging
import ssl
import urllib.error
import urllib.parse
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from reacher.cli import app
from reacher.db import connect
from reacher.sources import scb
from reacher.sources.base import RawSalon, SalonSource
from reacher.sources.bolagsverket import Response
from reacher.sources.config import SCB_API_KEY
from reacher.sources.scb import (
    API_URL,
    ScbApiSource,
    ScbAuthError,
    ScbClient,
    ScbError,
    _code,
    _date,
    _text,
    in_maintenance_window,
    to_raw_salon,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCB_DIR = Path(__file__).parent / "fixtures" / "scb"


def load(name: str) -> dict:
    return json.loads((SCB_DIR / name).read_text(encoding="utf-8"))


PAGE_1 = load("ae_naringsgren_96210.json")  # hasMore: true, nextCursorId 4711
AE_SOLE = load("ae_full.json")  # cfarNr 90000003
JE_SOLE = load("je_full.json")  # 198013012342, enskild firma

# Påhittat. Ser ut som en nyckel så att ett läckage syns i assert-meddelandet.
KEY = "test-scb-key-0000"
AB = "165599001236"  # aktiebolaget på sidan 1, två arbetsställen
SOLE = "198013012342"  # personnumret har månad 13, kan aldrig vara någons

PAGE_2 = {
    "pagination": {"nextCursorId": 4712, "limit": 3, "hasMore": False},
    "arbetsstallen": [],
}
AE_AB = {**AE_SOLE, "cfarNr": 90000001, "peOrgNr": AB, "ben": "Påhittade Saxen Söder"}
AE_AB["tel"] = "08-46500402"
JE_AB = {
    **JE_SOLE,
    "peOrgNr": AB,
    "orgNr": AB[2:],
    "namn": "Påhittade Saxen AB",
    "foretagsnamn": "Påhittade Saxen AB",
    "jurform": "49",
    "kommunSate": "0180",
    "tel": "08-46500403",
    "arbGivStat": "1",
}


@dataclass
class Call:
    url: str
    headers: dict[str, str]

    @property
    def path(self) -> str:
        return urllib.parse.urlsplit(self.url).path.removeprefix("/v1")

    @property
    def query(self) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.url).query))


class FakeScb:
    """SCB i minnet: två sidor för 96210, inga salonger för andra koder."""

    def __init__(self, workplaces=None, companies=None) -> None:
        self.workplaces = workplaces if workplaces is not None else {
            "90000001": AE_AB,
            "90000003": AE_SOLE,
        }  # fmt: skip
        self.companies = companies if companies is not None else {AB: JE_AB, SOLE: JE_SOLE}
        self.calls: list[Call] = []

    def paths(self, prefix: str) -> list[str]:
        return [c.path for c in self.calls if c.path.startswith(prefix)]

    def __call__(self, method, url, headers, body) -> Response:
        assert method == "GET" and body is None
        assert url.startswith(API_URL)
        call = Call(url, dict(headers))
        self.calls.append(call)
        assert headers["X-API-Key"] == KEY
        parts = call.path.strip("/").split("/")
        if parts[:2] == ["arbetsstallen", "naringsgren"]:
            if parts[2] != "96210":
                return ok({"pagination": {"nextCursorId": 0, "limit": 3, "hasMore": False},
                           "arbetsstallen": []})  # fmt: skip
            return ok(PAGE_2 if call.query.get("cursorId") == "4711" else PAGE_1)
        if parts[0] == "arbetsstallen" and parts[2] == "full":
            return found(self.workplaces.get(parts[1]))
        if parts[0] == "juridiskaenheter" and parts[2] == "full":
            return found(self.companies.get(parts[1]))
        raise AssertionError(f"oväntat anrop: {call.path}")


class Scripted:
    """Svarar med svaren i tur och ordning. Ett undantag i listan kastas."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, method, url, headers, body) -> Response:
        self.calls += 1
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


def ok(data) -> Response:
    return Response(200, {}, json.dumps(data).encode())


def found(data) -> Response:
    return ok(data) if data is not None else Response(404, {}, b"")


def client(send, clock=None, **kwargs) -> ScbClient:
    clock = clock or FakeClock()
    return ScbClient(SecretStr(KEY), send=send, sleep=clock.sleep, clock=clock, **kwargs)


def source(api, sni=("96210",), municipalities=("0180",)) -> ScbApiSource:
    return ScbApiSource(client(api), sni, municipalities, page_limit=3)


def salons(api, **kwargs) -> dict[str, RawSalon]:
    """Salongerna per cfar."""
    return {s.cfar: s for s in source(api, **kwargs).fetch()}


# --- Hämtning ----------------------------------------------------------------


def test_source_satisfies_the_protocol():
    assert isinstance(source(FakeScb()), SalonSource)


def test_fetch_is_lazy():
    """Inget anrop förrän någon läser: ingest drar en salong i taget."""
    api = FakeScb()
    salons_iter = source(api).fetch()
    assert api.calls == []
    next(salons_iter)
    assert api.calls


def test_paginates_with_the_cursor_and_the_same_limit():
    api = FakeScb()
    list(source(api).fetch())
    pages = [c.query for c in api.calls if "naringsgren" in c.path]
    assert pages == [{"limit": "3"}, {"limit": "3", "cursorId": "4711"}]


def test_a_repeated_cursor_stops_instead_of_looping():
    stuck = {"pagination": {"nextCursorId": 4711, "limit": 3, "hasMore": True},
             "arbetsstallen": []}  # fmt: skip
    api = Scripted(ok(stuck), ok(stuck))
    with pytest.raises(ScbError, match="samma cursor"):
        list(source(api).fetch())


def test_every_sni_code_gets_its_own_list():
    api = FakeScb()
    list(source(api, sni=("96210", "96220")).fetch())
    assert api.paths("/arbetsstallen/naringsgren/96220") == ["/arbetsstallen/naringsgren/96220"]


def test_only_workplaces_in_the_configured_municipality_are_kept():
    """Arbetsstället i Solna (0184) hämtas aldrig i detalj."""
    api = FakeScb()
    src = source(api)
    assert {s.cfar for s in src.fetch()} == {"90000001", "90000003"}
    assert "/arbetsstallen/90000002/full" not in api.paths("/arbetsstallen/9")
    assert (src.stats.listed, src.stats.kept) == (3, 2)


def test_each_company_is_fetched_once_per_run():
    """Aktiebolaget har salonger i både 0180 och 0184."""
    api = FakeScb(workplaces={"90000001": AE_AB, "90000002": AE_AB, "90000003": AE_SOLE})
    src = source(api, municipalities=("0180", "0184"))
    assert len(list(src.fetch())) == 3
    assert api.paths("/juridiskaenheter/") == [
        f"/juridiskaenheter/{AB}/full",
        f"/juridiskaenheter/{SOLE}/full",
    ]
    assert src.stats.companies == 2


def test_stats_count_phones():
    src = source(FakeScb())
    list(src.fetch())
    assert src.stats.with_phone == 2


# --- Fältmappning (docs/scb-fields.md) ---------------------------------------


def test_sole_trader_is_mapped_field_by_field():
    assert salons(FakeScb())["90000003"] == RawSalon(
        orgnr=SOLE,  # 12 siffror: ingest normaliserar
        name="Fiktivsson, Fiktiv",  # ben är " " och foretagsnamn "", så juridiskt namn
        cfar="90000003",
        sni="96210",
        street="Fiktiva torget 3",
        postal_code="11860",
        city="STOCKHOLM",
        municipality="0180",
        employee_class="2",
        registered_at=date(2025, 1, 20),
        website=None,
        phone="08-46500401",  # arbetsstället saknar nummer, så företagets
        legal_form="10",
        ftax_status="1",
        vat_status="1",
        employer_status="0",
        company_status="1",
        workplace_status="1",
        ad_block_type="1",
        phone_block_type="1",
        workplace_ad_block_type="1",
        workplace_phone_block_type="1",
    )


def test_address_and_municipality_come_from_the_workplace():
    """D19: postAdress och kommunSate är ägarens hem för en enskild firma."""
    salon = salons(FakeScb())["90000003"]
    assert salon.street != JE_SOLE["postAdress"]["gatuAdress"]
    assert salon.postal_code != AE_SOLE["postAdress"]["postNr"]
    assert salon.municipality != JE_SOLE["kommunSate"]


def test_salon_name_comes_first_and_workplace_phone_wins():
    salon = salons(FakeScb())["90000001"]
    assert salon.name == "Påhittade Saxen Söder"
    assert salon.phone == "08-46500402"
    assert salon.legal_form == "49"


def test_every_code_is_text():
    """reklamSparrTyp och telefonSparrTyp är tal i JSON, ftgStat en sträng: alla blir text."""
    for salon in salons(FakeScb()).values():
        for field in ("cfar", "employee_class", "workplace_status", "ad_block_type",
                      "phone_block_type", "workplace_ad_block_type",
                      "workplace_phone_block_type", "company_status", "legal_form"):  # fmt: skip
            assert isinstance(getattr(salon, field), str), field


def test_unknown_company_gives_unknown_compliance_fields():
    """SCB svarar 404: inga påhittade koder, så callable_salon utesluter salongen."""
    salon = salons(FakeScb(companies={}))["90000003"]
    assert salon.name == ""  # inget ben, inget namn: ingest avvisar den
    assert (salon.legal_form, salon.ftax_status, salon.company_status) == (None, None, None)
    assert salon.workplace_status == "1"  # arbetsställets egna fält finns kvar


def test_codes_text_and_blanks():
    assert _code(1) == "1" and _code("1") == "1"
    assert _code(None) is None and _code("") is None and _code(" ") is None
    assert _code("01") == "01"  # str() och inget annat: inledande nolla behålls
    assert _text(" ") is None and _text(" Saxen ") == "Saxen"


def test_dates_are_swedish_dates():
    assert _date("2025-02-02T23:00:00.000Z") == date(2025, 2, 3)  # vintertid
    assert _date("2025-06-01T22:00:00.000Z") == date(2025, 6, 2)  # sommartid
    assert _date("2025-01-20T15:16:00.000Z") == date(2025, 1, 20)
    assert _date("") is None and _date(None) is None


def test_broken_date_is_dropped_with_a_warning(caplog):
    assert _date("igår") is None
    assert "igår" not in caplog.text


def test_mapping_without_full_responses():
    salon = to_raw_salon(PAGE_1["arbetsstallen"][0], None, None)
    assert salon.phone is None and salon.registered_at is None
    assert salon.workplace_phone_block_type == "1"


def test_maintenance_window_is_swedish_time():
    assert in_maintenance_window(datetime(2026, 1, 15, 3, 10, tzinfo=UTC))  # 04:10 i Sverige
    assert not in_maintenance_window(datetime(2026, 1, 15, 3, 40, tzinfo=UTC))
    assert in_maintenance_window(datetime(2026, 7, 15, 2, 0, tzinfo=UTC))  # 04:00 sommartid


# --- Klienten: nyckel, fel och omförsök ---------------------------------------


def test_key_is_sent_in_the_header_never_in_the_url():
    api = FakeScb()
    list(source(api).fetch())
    assert all(KEY not in c.url for c in api.calls)
    assert all(c.headers["X-API-Key"] == KEY for c in api.calls)


def test_wrong_key_stops_at_once():
    clock = FakeClock()
    api = Scripted(Response(401, {}, b'{"title": "Invalid or missing API key"}'))
    with pytest.raises(ScbAuthError, match="SCB_API_KEY") as error:
        list(ScbApiSource(client(api, clock), ["96210"], ["0180"]).fetch())
    assert KEY not in str(error.value)
    assert api.calls == 1 and clock.sleeps == []


def test_server_errors_are_retried_with_backoff():
    clock = FakeClock()
    api = Scripted(Response(503, {}, b""), Response(502, {}, b""), ok(JE_SOLE))
    assert client(api, clock, min_interval=0).company(SOLE) == JE_SOLE
    assert clock.sleeps == [2.0, 4.0]


def test_retry_after_is_respected():
    clock = FakeClock()
    api = Scripted(Response(429, {"retry-after": "7"}, b""), ok(JE_SOLE))
    client(api, clock, min_interval=0).company(SOLE)
    assert clock.sleeps == [7.0]


def test_network_errors_are_retried():
    clock = FakeClock()
    api = Scripted(TimeoutError(), ConnectionResetError(), ok(JE_SOLE))
    assert client(api, clock, min_interval=0).company(SOLE) == JE_SOLE
    assert clock.sleeps == [2.0, 4.0]


def test_certificate_error_is_not_retried():
    clock = FakeClock()
    bad_cert = urllib.error.URLError(ssl.SSLCertVerificationError(1, "verify failed"))
    with pytest.raises(ScbError, match="TLS-certifikatet kunde inte verifieras"):
        client(Scripted(bad_cert), clock, min_interval=0).company(SOLE)
    assert clock.sleeps == []


def test_gives_up_without_showing_the_orgnr(caplog):
    """Personnumret står i URL:en men aldrig i felet eller loggen."""
    caplog.set_level(logging.DEBUG)
    clock = FakeClock()
    api = Scripted(*[Response(500, {}, f'{{"instance": "{SOLE}"}}'.encode())] * 4)
    with pytest.raises(ScbError, match="efter 4 försök") as error:
        client(api, clock, min_interval=0).company(SOLE)
    assert clock.sleeps == [2.0, 4.0, 8.0]
    for shown in (str(error.value), caplog.text):
        assert SOLE not in shown and SOLE[2:] not in shown and KEY not in shown
    assert "/juridiskaenheter/{peOrgNr}/full" in str(error.value)


def test_client_errors_are_not_retried():
    api = Scripted(Response(400, {}, f'{{"instance": "{SOLE}"}}'.encode()))
    with pytest.raises(ScbError, match="HTTP 400") as error:
        client(api).company(SOLE)
    assert SOLE not in str(error.value)
    assert api.calls == 1


def test_requests_are_spaced():
    clock = FakeClock()
    api = Scripted(ok(JE_SOLE), ok(JE_SOLE))
    c = client(api, clock, min_interval=0.25)
    c.company(SOLE)
    c.company(SOLE)
    assert clock.sleeps == [0.25]


def test_unknown_workplace_or_company_is_none():
    api = Scripted(Response(404, {}, b""), Response(404, {}, b""))
    c = client(api)
    assert c.workplace("90000099") is None
    assert c.company(SOLE) is None


# --- CLI: reacher ingest --source scb ------------------------------------------

runner = CliRunner()


def ingest_scb(db: Path):
    config = str(REPO_ROOT / "sources.yaml")
    return runner.invoke(app, ["ingest", "--source", "scb", "--db", str(db), "--config", config])


@pytest.fixture
def fake_scb(monkeypatch):
    monkeypatch.setenv(SCB_API_KEY, KEY)
    api = FakeScb()
    monkeypatch.setattr(scb, "urllib_send", api)
    monkeypatch.setattr(scb.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(scb, "in_maintenance_window", lambda moment: False)
    return api


def test_ingest_scb_needs_the_key(monkeypatch, tmp_path):
    monkeypatch.delenv(SCB_API_KEY, raising=False)
    result = ingest_scb(tmp_path / "t.db")
    assert result.exit_code == 1
    assert f"{SCB_API_KEY} saknas" in result.output


def test_ingest_scb_refuses_to_run_during_maintenance(fake_scb, monkeypatch, tmp_path):
    monkeypatch.setattr(scb, "in_maintenance_window", lambda moment: True)
    result = ingest_scb(tmp_path / "t.db")
    assert result.exit_code == 1
    assert "04:30" in result.output
    assert fake_scb.calls == []


def test_ingest_scb_end_to_end_and_again(fake_scb, tmp_path):
    db = tmp_path / "scb.db"
    first = ingest_scb(db)
    assert first.exit_code == 0, first.output
    assert "2 nya, 0 uppdaterade, 0 avvisade" in first.output
    assert "SCB: 3 arbetsställen i SNI 96210, 96220, 2 i kommun 0180" in first.output
    assert "2 av 2 med telefonnummer" in first.output

    second = ingest_scb(db)
    assert "0 nya, 2 uppdaterade" in second.output  # idempotent

    for shown in (first.output, second.output):
        assert SOLE not in shown and SOLE[2:] not in shown and KEY not in shown
    with closing(connect(db)) as conn:
        rows = conn.execute(
            "SELECT s.orgnr, s.cfar, s.municipality, c.value AS phone FROM callable_salon s "
            "JOIN contact c ON c.salon_id = s.id AND c.kind = 'phone' ORDER BY s.cfar"
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        (AB[2:], "90000001", "0180", "+46846500402"),
        (SOLE[2:], "90000003", "0180", "+46846500401"),
    ]


def test_ingest_scb_saves_nothing_when_scb_is_down(fake_scb, monkeypatch, tmp_path):
    monkeypatch.setattr(scb, "urllib_send", lambda *args: Response(503, {}, b""))
    db = tmp_path / "scb.db"
    result = ingest_scb(db)
    assert result.exit_code == 1
    assert "efter 4 försök. Inget sparades." in result.output
    with closing(connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM salon").fetchone()[0] == 0
