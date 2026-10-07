"""T1-05: callable_salon. Det mest compliance-kritiska i projektet.

Två sorters tester:
- Mot fixturerna: varje kantfall i tests/fixtures/README.md hamnar på rätt sida.
- Mot handgjorda rader: acceptanskriterierna i #14 och #47, en regel i taget.
"""

from contextlib import closing
from pathlib import Path

import pytest

from reacher.db import connect, migrate
from reacher.ingest import ingest
from reacher.sources.csv_source import CsvSource

FIXTURES = Path(__file__).parent / "fixtures"
NOW = "2026-10-01T08:00:00+00:00"

# Salonger i fixturerna som INTE får ringas (se kantfallstabellen i README).
EXCLUDED_IN_FIXTURES = {
    "Kedjan Klipp Solna",  # bara arbetsstället har reklamspärr (2)
    "Oklara Salongen",  # juridisk form 99, allt 0 (beslut 3)
    "Mystiska Salongen",  # juridisk form saknas, allt okänt
    "Salong Solo 6",  # bara privat arbetsgivare (beslut 2)
    "Salong Solo 7",  # aldrig registrerad
    "Salong Solo 8",  # allt avregistrerat
    "Salong Solo 9",  # allt okänt
    "Salong Solo 11",  # okänt/avregistrerat/privat
    "Salong Solo 14",  # avregistrerad F-skatt, ingen moms, okänd arbetsgivare
    "Reklamtest 12",
    "Reklamtest 13",  # NIX-Telefon (beslut 1)
    "Reklamtest 21",
    "Reklamtest 22",
    "Reklamtest 23",
    "Reklamtest tom",
    "Arbetsställe Okänt",
    "Arbetsställe Spärrat",
    "Arbetsställe NIX",  # bara arbetsstället har NIX-Telefon (D28)
    "Okänd Spärrkod AB",  # reklamSparrTyp 7 finns inte
    "Nystartade Drömsalongen",  # högt poäng men spärrad
    "Vilande Salongen AB",  # ftgStat 0 (D31)
    "Avvecklade Salongen AB",  # ftgStat 9 (D31)
    "Statuslösa Salongen AB",  # ftgStat saknas (D31)
    "Dödsboets Salong",  # juridisk form 91, trots F-skatt (D30)
    "Kedjan Klipp Nedlagd",  # verksamt företag, nedlagd salong (D33)
    "Aldrig Öppnade Salongen",  # aeStat 0 (D33)
    "Arbetsställe Utan Status",  # aeStat saknas (D33)
}


@pytest.fixture
def conn(tmp_path):
    with closing(connect(tmp_path / "t.db")) as c:
        migrate(c)
        yield c


@pytest.fixture
def seeded(conn):
    ingest(conn, CsvSource(FIXTURES).fetch(), now=NOW)
    return conn


def callable_names(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM callable_salon")}


def all_names(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM salon")}


def block(conn, orgnr: str, reason: str = "opt_out") -> None:
    conn.execute(
        "INSERT INTO suppression (orgnr, reason, created_at) VALUES (?, ?, ?)",
        (orgnr, reason, NOW),
    )


# --- Mot fixturerna ----------------------------------------------------------


def test_fixture_exclusions_match_the_readme(seeded):
    assert all_names(seeded) - callable_names(seeded) == EXCLUDED_IN_FIXTURES


def test_fixture_callable_count(seeded):
    """75 salonger efter ingest, 27 utesluts. Ändras siffran har en regel ändrats."""
    count = seeded.execute("SELECT count(*) FROM callable_salon").fetchone()[0]
    assert count == 48


@pytest.mark.parametrize(
    "name",
    [
        "Syskonen Sax HB",  # HB, allt okänt: NIX-regeln gäller inte
        "Okända Salongen AB",  # AB, allt okänt, inga spärrar
        "Nollställda AB",  # AB, allt 0
        "Oklara Men Seriösa",  # juridisk form 99 men F-skatt
        "Salong Solo 1",  # bara F-skatt
        "Salong Solo 2",  # bara moms
        "Salong Solo 3",  # bara moms via ombud
        "Salong Solo 4",  # bara arbetsgivare
        "Salong Solo 5",  # bara arbetsgivare via ombud
        "Salong Solo 10",  # F-skatt, resten okänt
        "Salong Solo 12",  # avregistrerad F-skatt men moms
        "Kedjan Klipp Söder",  # samma orgnr som Solna, men eget arbetsställe utan spärr
        "Formlösa Med F-skatt",  # juridisk form saknas, F-skatt: D30 rör den inte (D22)
    ],
)
def test_fixture_callable_edge_cases(seeded, name):
    assert name in callable_names(seeded)


def test_view_has_the_same_columns_as_salon(conn):
    """Team 2 läser vyn som om den vore salon (SalonFacts.from_row)."""
    salon = [r["name"] for r in conn.execute("PRAGMA table_info(salon)")]
    view = [r["name"] for r in conn.execute("PRAGMA table_info(callable_salon)")]
    assert view == salon


# --- Spärrlistan -------------------------------------------------------------


def test_opt_out_removes_every_workplace_of_the_orgnr(seeded):
    """Kedjan Klipp har fyra arbetsställen. En spärr gäller alla (D7)."""
    before = seeded.execute(
        "SELECT count(*) FROM callable_salon WHERE orgnr = '5590001011'"
    ).fetchone()[0]
    assert before == 2  # Solna (reklamspärr) och Nedlagd (D33) är redan uteslutna

    block(seeded, "5590001011")
    after = seeded.execute(
        "SELECT count(*) FROM callable_salon WHERE orgnr = '5590001011'"
    ).fetchone()[0]
    assert after == 0


def test_block_after_ingest_applies_without_reingest(seeded):
    """Filtrering vid läsning: ingen ny ingest behövs för att spärren ska gälla."""
    assert "Salong Dubbelgångaren" in callable_names(seeded)
    block(seeded, "5560002023")
    assert "Salong Dubbelgångaren" not in callable_names(seeded)


@pytest.mark.parametrize("reason", ["opt_out", "existing_customer", "no_ftax"])
def test_every_suppression_reason_excludes(seeded, reason):
    block(seeded, "5560009549", reason)  # Lockgården
    assert "Lockgården" not in callable_names(seeded)


def test_block_on_another_orgnr_changes_nothing(seeded):
    before = callable_names(seeded)
    block(seeded, "5569999999")
    assert callable_names(seeded) == before


# --- En regel i taget (acceptanskriterierna i #14 och #47) -------------------

# Utgångsläge: ett verksamt AB utan spärrar, som är ringbart. Varje fall ändrar
# bara de fält som testet handlar om.
CALLABLE_AB = {
    "legal_form": "49",
    "ftax_status": "1",
    "vat_status": "1",
    "employer_status": "1",
    "company_status": "1",
    "workplace_status": "1",
    "ad_block_type": "1",
    "phone_block_type": "1",
    "workplace_ad_block_type": "1",
    "workplace_phone_block_type": "1",
}
NOTHING_REGISTERED = {"ftax_status": None, "vat_status": None, "employer_status": None}
ONLY_FTAX = {**NOTHING_REGISTERED, "ftax_status": "1"}

# D28: varje spärrfält, på båda nivåerna, med varje kod. Bara 1 är ringbart.
BLOCK_CASES = [
    (f"{field}-{code or 'null'}", {field: code}, code == "1")
    for field in (
        "ad_block_type",
        "phone_block_type",
        "workplace_ad_block_type",
        "workplace_phone_block_type",
    )
    for code in ("1", "2", "3", None, "7", "11")  # 11 = gamla koden, ska inte gå igenom
]

CASES = [
    # (id, ändringar mot CALLABLE_AB, ringbar?)
    *BLOCK_CASES,
    # D31: bara verksamma företag.
    ("company-status-1", {"company_status": "1"}, True),
    ("company-status-0-never-active", {"company_status": "0"}, False),
    ("company-status-9-no-longer-active", {"company_status": "9"}, False),
    ("company-status-null", {"company_status": None}, False),
    ("company-status-unknown", {"company_status": "5"}, False),
    # D33: bara verksamma arbetsställen, även hos ett verksamt företag.
    ("workplace-status-1", {"workplace_status": "1"}, True),
    ("workplace-status-0-never-active", {"workplace_status": "0"}, False),
    ("workplace-status-9-no-longer-active", {"workplace_status": "9"}, False),
    ("workplace-status-null", {"workplace_status": None}, False),
    ("workplace-status-unknown", {"workplace_status": "5"}, False),
    # D30: dödsbon aldrig, men en saknad juridisk form avgörs fortfarande av D22.
    ("estate-91-everything-registered", {"legal_form": "91"}, False),
    ("estate-91-only-ftax", {"legal_form": "91", **ONLY_FTAX}, False),
    ("legal-form-null-with-ftax", {"legal_form": None, **ONLY_FTAX}, True),
    ("legal-form-null-nothing-known", {"legal_form": None, **NOTHING_REGISTERED}, False),
    # NIX 6.3 (D11, D21, D22), oförändrat från T1-05.
    ("ab-null-statuses", NOTHING_REGISTERED, True),
    ("sole-prop-only-vat-1", {"legal_form": "10", **NOTHING_REGISTERED, "vat_status": "1"}, True),
    ("sole-prop-only-vat-3", {"legal_form": "10", **NOTHING_REGISTERED, "vat_status": "3"}, True),
    ("sole-prop-only-ftax-1", {"legal_form": "10", **ONLY_FTAX}, True),
    (
        "sole-prop-only-employer-1",
        {"legal_form": "10", **NOTHING_REGISTERED, "employer_status": "1"},
        True,
    ),
    (
        "sole-prop-only-employer-3",
        {"legal_form": "10", **NOTHING_REGISTERED, "employer_status": "3"},
        True,
    ),
    (
        "sole-prop-only-ftax-9-deregistered",
        {"legal_form": "10", **NOTHING_REGISTERED, "ftax_status": "9"},
        False,
    ),
    (
        "sole-prop-private-employer-2",
        {"legal_form": "10", **NOTHING_REGISTERED, "employer_status": "2"},
        False,
    ),
    (
        "sole-prop-unknown-ftax-code-5",
        {"legal_form": "10", **NOTHING_REGISTERED, "ftax_status": "5"},
        False,
    ),
    ("sole-prop-nothing-known", {"legal_form": "10", **NOTHING_REGISTERED}, False),
    ("legal-form-99-nothing-known", {"legal_form": "99", **NOTHING_REGISTERED}, False),
    ("legal-form-unknown-code", {"legal_form": "77", **NOTHING_REGISTERED}, False),
    ("hb-nothing-known", {"legal_form": "31", **NOTHING_REGISTERED}, True),
    ("sole-prop-phone-blocked", {"legal_form": "10", "phone_block_type": "2"}, False),
]


@pytest.mark.parametrize(("changes", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_rule(conn, changes, expected):
    fields = {**CALLABLE_AB, **changes}
    conn.execute(
        "INSERT INTO salon (orgnr, name, first_seen_at, last_seen_at, "
        + ", ".join(fields)
        + ") VALUES ('5561234567', 'Testsalongen', :now, :now, "
        + ", ".join(":" + f for f in fields)
        + ")",
        {**fields, "now": NOW},
    )
    assert (callable_names(conn) == {"Testsalongen"}) is expected
