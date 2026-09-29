"""T1-05: callable_salon. Det mest compliance-kritiska i projektet.

Två sorters tester:
- Mot fixturerna: varje kantfall i tests/fixtures/README.md hamnar på rätt sida.
- Mot handgjorda rader: acceptanskriterierna i #14, en regel i taget.
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
    "Kedjan Klipp Solna",  # bara arbetsstället har reklamspärr (21)
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
    "Nystartade Drömsalongen",  # högt poäng men spärrad
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
    """65 salonger efter ingest, 18 utesluts. Ändras siffran har en regel ändrats."""
    count = seeded.execute("SELECT count(*) FROM callable_salon").fetchone()[0]
    assert count == 47


@pytest.mark.parametrize(
    "name",
    [
        "Syskonen Sax HB",  # HB, allt okänt: NIX-regeln gäller inte
        "Okända Salongen AB",  # AB, allt okänt, ad 11
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
    """Kedjan Klipp har tre arbetsställen. En spärr gäller alla (D7)."""
    before = seeded.execute(
        "SELECT count(*) FROM callable_salon WHERE orgnr = '5590001011'"
    ).fetchone()[0]
    assert before == 2  # Solna är redan utesluten av sin reklamspärr

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


# --- En regel i taget (acceptanskriterierna i #14) ---------------------------

# Utgångsläge: ett AB utan reklamspärr, som är ringbart. Varje fall ändrar
# bara de fält som testet handlar om.
CALLABLE_AB = {
    "legal_form": "49",
    "ftax_status": "1",
    "vat_status": "1",
    "employer_status": "1",
    "ad_status": "11",
    "workplace_ad_status": "11",
}
NOTHING_REGISTERED = {"ftax_status": None, "vat_status": None, "employer_status": None}

CASES = [
    # (id, ändringar mot CALLABLE_AB, ringbar?)
    ("ab-null-statuses-ad-11", NOTHING_REGISTERED, True),
    ("ab-null-ad-status", {"ad_status": None}, False),
    ("ab-null-workplace-ad-status", {"workplace_ad_status": None}, False),
    ("ad-11-workplace-22", {"workplace_ad_status": "22"}, False),
    ("ad-13-nix-telefon", {"ad_status": "13"}, False),
    ("ad-12-telemarketing-block", {"ad_status": "12"}, False),
    ("ad-21-opted-out", {"ad_status": "21"}, False),
    ("ad-unknown-code", {"ad_status": "99"}, False),
    ("sole-prop-only-vat-1", {"legal_form": "10", **NOTHING_REGISTERED, "vat_status": "1"}, True),
    ("sole-prop-only-vat-3", {"legal_form": "10", **NOTHING_REGISTERED, "vat_status": "3"}, True),
    ("sole-prop-only-ftax-1", {"legal_form": "10", **NOTHING_REGISTERED, "ftax_status": "1"}, True),
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
    ("legal-form-null-nothing-known", {"legal_form": None, **NOTHING_REGISTERED}, False),
    ("legal-form-unknown-code", {"legal_form": "77", **NOTHING_REGISTERED}, False),
    ("hb-nothing-known", {"legal_form": "31", **NOTHING_REGISTERED}, True),
    ("sole-prop-ad-blocked", {"legal_form": "10", "ad_status": "22"}, False),
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
