"""T1-12: financial_fact, ingest av RawFinancial och vyn latest_financial_fact.

Siffrorna följer tabellen "Finansiella fakta" i tests/fixtures/README.md:
36 rader i financials.csv, 1 med ogiltigt orgnr -> 35 fakta för 8 företag.
"""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reacher.cli import app
from reacher.db import connect, migrate
from reacher.ingest import ingest_financials
from reacher.sources.base import FinancialSource, RawFinancial
from reacher.sources.config import SourcesConfig
from reacher.sources.csv_source import CsvFinancialSource

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
T1 = "2026-10-01T08:00:00+00:00"
T2 = "2026-10-08T08:00:00+00:00"

KEDJAN = "5590001011"  # 4 räkenskapsår, 4 salonger
DUBBEL = "5560002023"  # förlust senaste året
HARATELJEN = "5560003039"  # saknar 2023
FRISYR = "5560005083"  # brutet räkenskapsår (30 april)
BRYNBODEN = "5560009531"  # net_result saknas senaste året
FRANS = "5590009550"  # ett enda år
KAMGARDEN = "5560009697"  # AB utan årsredovisning


@pytest.fixture
def conn(tmp_path):
    with closing(connect(tmp_path / "t.db")) as c:
        migrate(c)
        yield c


def load(conn, now=T1):
    return ingest_financials(conn, CsvFinancialSource(FIXTURES).fetch(), now=now)


def fact(value=100, orgnr=DUBBEL, key="revenue", period_end=date(2024, 12, 31), doc="D1"):
    return RawFinancial(
        orgnr=orgnr, period_end=period_end, key=key, value=value, source_document=doc
    )


def facts_of(conn, orgnr, table="financial_fact"):
    return {
        (r["period_end"], r["key"]): r["value"]
        for r in conn.execute(f"SELECT * FROM {table} WHERE orgnr = ?", (orgnr,))
    }


# --- Tabellen ----------------------------------------------------------------


def test_table_has_the_agreed_columns(conn):
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(financial_fact)")}
    assert list(cols) == [
        "orgnr",
        "period_end",
        "key",
        "value",
        "source_document",
        "fetched_at",
    ]
    assert cols["value"]["type"] == "INTEGER" and cols["value"]["notnull"] == 1
    assert cols["source_document"]["notnull"] == 0


@pytest.mark.parametrize("value", [1234567.5, "1 234 567", None])
def test_table_refuses_anything_but_whole_kronor(conn, value):
    """Sista skyddet om någon skriver förbi ingest_financials."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO financial_fact (orgnr, period_end, key, value, fetched_at) "
            "VALUES ('5560002023', '2024-12-31', 'revenue', ?, ?)",
            (value, T1),
        )


def test_one_value_per_company_year_and_key(conn):
    ins = (
        "INSERT INTO financial_fact (orgnr, period_end, key, value, fetched_at) "
        "VALUES ('5560002023', '2024-12-31', 'revenue', 1, ?)"
    )
    conn.execute(ins, (T1,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (T1,))


# --- Ingest ------------------------------------------------------------------


def test_first_run(conn):
    s = load(conn)
    assert (s.inserted, s.updated, s.unchanged, s.rejected) == (35, 0, 0, 1)


def test_second_run_gives_the_same_database(conn):
    def snapshot():
        return [dict(r) for r in conn.execute("SELECT * FROM financial_fact ORDER BY 1, 2, 3")]

    load(conn, now=T1)
    before = snapshot()
    s = load(conn, now=T2)
    assert (s.inserted, s.updated, s.unchanged, s.rejected) == (0, 0, 35, 1)
    assert snapshot() == before  # inklusive fetched_at: ett oförändrat värde rörs inte


def test_changed_value_is_updated_and_gets_a_new_fetched_at(conn):
    ingest_financials(conn, [fact(100, doc="D1")], now=T1)
    s = ingest_financials(conn, [fact(-250, doc="D2")], now=T2)
    assert (s.inserted, s.updated) == (0, 1)
    row = conn.execute("SELECT * FROM financial_fact").fetchone()
    assert (row["value"], row["source_document"], row["fetched_at"]) == (-250, "D2", T2)


def test_orgnr_is_normalised_like_salons(conn):
    load(conn)
    orgnrs = {r[0] for r in conn.execute("SELECT DISTINCT orgnr FROM financial_fact")}
    # Kedjan, Dubbelgångaren och Hårateljén står med bindestreck eller 16-prefix i filen.
    assert {KEDJAN, DUBBEL, HARATELJEN} <= orgnrs
    assert len(orgnrs) == 8
    assert all(len(o) == 10 and o.isdigit() for o in orgnrs)


def test_invalid_orgnr_is_never_inserted(conn):
    load(conn)
    assert facts_of(conn, "5560004046") == {}  # Felaktiga AB, fel kontrollsiffra


@pytest.mark.parametrize(
    "raw",
    [
        fact(orgnr=""),
        fact(orgnr="5560004046"),
        fact(key=""),
        fact(key="  "),
        fact(value=None),
        fact(value=1234567.0),
        fact(value="1234567"),
        fact(value=True),
    ],
    ids=["no-orgnr", "bad-orgnr", "no-key", "blank-key", "none", "float", "text", "bool"],
)
def test_invalid_fact_is_rejected_never_stored_as_zero(conn, raw):
    s = ingest_financials(conn, [raw], now=T1)
    assert (s.inserted, s.rejected) == (0, 1)
    assert conn.execute("SELECT count(*) FROM financial_fact").fetchone()[0] == 0


def test_rejection_log_never_shows_the_orgnr(conn, caplog):
    """En enskild firmas orgnr är ett personnummer."""
    ingest_financials(conn, [fact(value=None, orgnr="198503122148")], now=T1)
    assert "Finansiell post 1 avvisad" in caplog.text
    assert "8503122148" not in caplog.text


def test_missing_value_is_no_row_never_zero(conn):
    load(conn)
    brynboden = facts_of(conn, BRYNBODEN)
    assert brynboden[("2024-12-31", "revenue")] == 1567800
    assert ("2024-12-31", "net_result") not in brynboden
    assert facts_of(conn, HARATELJEN).keys() == {
        ("2024-12-31", "revenue"),
        ("2024-12-31", "net_result"),
        ("2022-12-31", "revenue"),
        ("2022-12-31", "net_result"),
    }


def test_company_without_reports_has_no_rows(conn):
    load(conn)
    assert facts_of(conn, KAMGARDEN) == {}


def test_values_keep_their_sign(conn):
    load(conn)
    assert facts_of(conn, DUBBEL)[("2024-12-31", "net_result")] == -87400
    assert facts_of(conn, "5590009576")[("2024-12-31", "net_result")] == 0  # noll är ett värde


def test_financial_facts_do_not_need_a_salon(conn):
    """Fakta är per företag. Salongen kan komma senare, eller ha försvunnit (T1-07)."""
    s = ingest_financials(conn, [fact(orgnr="5560010109")], now=T1)
    assert s.inserted == 1


# --- Vyn för Team 2 ----------------------------------------------------------


def test_latest_view_keeps_the_three_newest_years(conn):
    load(conn)
    years = {pe for pe, _ in facts_of(conn, KEDJAN, "latest_financial_fact")}
    assert years == {"2024-12-31", "2023-12-31", "2022-12-31"}  # 2021 faller bort
    assert conn.execute("SELECT count(*) FROM latest_financial_fact").fetchone()[0] == 33


def test_latest_view_keeps_as_many_years_as_sources_yaml(conn):
    """Vyn har 3 inskrivet; sources.yaml säger hur många år som hämtas. De ska vara lika."""
    years = SourcesConfig.load(REPO_ROOT / "sources.yaml").bolagsverket.years
    ingest_financials(
        conn, [fact(period_end=date(2010 + i, 12, 31)) for i in range(years + 2)], now=T1
    )
    assert conn.execute("SELECT count(*) FROM latest_financial_fact").fetchone()[0] == years


def test_rank_is_per_company_not_per_key(conn):
    """Brynboden saknar net_result för 2024. Rang 1 är ändå 2024, så Resultat visar
    inte 2023 bredvid Omsättning 2024, och loss_making räknar inte på ett äldre år."""
    load(conn)
    rows = conn.execute(
        "SELECT period_end, key, fiscal_year_rank FROM latest_financial_fact "
        "WHERE orgnr = ? ORDER BY fiscal_year_rank, key",
        (BRYNBODEN,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("2024-12-31", "revenue", 1),
        ("2023-12-31", "net_result", 2),
        ("2023-12-31", "revenue", 2),
    ]


def test_missing_year_is_skipped_not_filled(conn):
    load(conn)
    ranks = {
        (r["period_end"], r["fiscal_year_rank"])
        for r in conn.execute("SELECT * FROM latest_financial_fact WHERE orgnr = ?", (HARATELJEN,))
    }
    assert ranks == {("2024-12-31", 1), ("2022-12-31", 2)}


def test_team_2_can_join_on_orgnr(conn):
    """Så som T2-09 läser: ringbara salonger + senaste årets resultat."""
    from reacher.cli import FIXTURES_DIR
    from reacher.ingest import ingest
    from reacher.sources.csv_source import CsvSource

    ingest(conn, CsvSource(FIXTURES_DIR).fetch(), now=T1)
    load(conn)
    rows = conn.execute(
        "SELECT s.name, f.value FROM callable_salon AS s "
        "JOIN latest_financial_fact AS f "
        "  ON f.orgnr = s.orgnr AND f.key = 'net_result' AND f.fiscal_year_rank = 1 "
        "WHERE f.value < 0"
    ).fetchall()
    assert {(r["name"], r["value"]) for r in rows} == {
        ("Salong Dubbelgångaren", -87400),
        ("Franssalongen", -45300),
    }


# --- Källan ------------------------------------------------------------------


def test_csv_financial_source_satisfies_the_protocol():
    assert isinstance(CsvFinancialSource(FIXTURES), FinancialSource)


def test_csv_source_passes_values_through_raw():
    first = next(iter(CsvFinancialSource(FIXTURES).fetch()))
    assert first == RawFinancial(
        orgnr="559000-1011",  # normaliseras vid ingest, inte i källan
        period_end=date(2024, 12, 31),
        key="revenue",
        value=4812300,
        source_document="FIKTIV-0101",
    )


@pytest.mark.parametrize(
    ("row", "error"),
    [
        ("5560002023,2024-12-31,revenue,,D1", "value och period_end krävs"),
        ("5560002023,,revenue,100,D1", "value och period_end krävs"),
        ("5560002023,2024-12-31,revenue,1234567.0,D1", "invalid literal"),
        ("5560002023,2024-12-31,revenue,1 234 567,D1", "invalid literal"),
    ],
    ids=["empty-value", "empty-date", "decimal", "spaces"],
)
def test_broken_fixture_row_stops_the_source(tmp_path, row, error):
    (tmp_path / "financials.csv").write_text(
        f"orgnr,period_end,key,value,source_document\n{row}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match=error):
        list(CsvFinancialSource(tmp_path).fetch())


def test_wrong_header_is_refused(tmp_path):
    (tmp_path / "financials.csv").write_text("orgnr;period_end;key;value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rubrikraden"):
        list(CsvFinancialSource(tmp_path).fetch())


def test_load_seed_loads_financials_twice_via_cli(tmp_path):
    runner = CliRunner()
    db = str(tmp_path / "cli.db")
    first = runner.invoke(app, ["load-seed", "--db", db])
    second = runner.invoke(app, ["load-seed", "--db", db])
    assert first.exit_code == 0 and second.exit_code == 0
    assert "35 nya, 0 ändrade, 0 oförändrade, 1 avvisade finansiella fakta" in first.stdout
    assert "0 nya, 0 ändrade, 35 oförändrade, 1 avvisade finansiella fakta" in second.stdout
