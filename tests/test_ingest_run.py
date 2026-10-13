"""T1-07: salonger som försvinner ur SCB (D38).

Varje sparad ingest är en körning i ingest_run, och callable_salon släpper bara
igenom salonger från den senaste. Kraschade och tomma körningar sparas inte, så
de kan aldrig få salonger att försvinna.
"""

import shutil
from contextlib import closing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reacher.cli import app
from reacher.db import MIGRATIONS_DIR, connect, migrate
from reacher.ingest import EmptyRunError, ingest
from reacher.sources.base import CSV_COLUMNS, SIGNAL_CSV_COLUMNS, RawSalon
from reacher.sources.csv_source import CsvSource

FIXTURES = Path(__file__).parent / "fixtures"
T1 = "2026-10-01T08:00:00+00:00"
T2 = "2026-10-08T08:00:00+00:00"
T3 = "2026-10-15T08:00:00+00:00"

# Ett verksamt AB utan spärrar: ringbart så länge det finns i den senaste körningen.
CALLABLE = {
    "legal_form": "49",
    "company_status": "1",
    "workplace_status": "1",
    "ad_block_type": "1",
    "phone_block_type": "1",
    "workplace_ad_block_type": "1",
    "workplace_phone_block_type": "1",
}


def salon(name: str, cfar: str, orgnr: str = "5561234567") -> RawSalon:
    return RawSalon(orgnr=orgnr, name=name, cfar=cfar, **CALLABLE)


SAX = salon("Saxen", "1")
KAMMEN = salon("Kammen", "2", orgnr="5590001011")


@pytest.fixture
def conn(tmp_path):
    with closing(connect(tmp_path / "t.db")) as c:
        migrate(c)
        yield c


def callable_names(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM callable_salon")}


def runs(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM ingest_run ORDER BY id")]


# --- Körningar ---------------------------------------------------------------


def test_every_ingest_is_recorded_as_a_run(conn):
    ingest(conn, CsvSource(FIXTURES).fetch(), now=T1, source="csv")
    ingest(conn, CsvSource(FIXTURES).fetch(), now=T2, source="csv")
    recorded = runs(conn)
    assert [(r["id"], r["source"], r["started_at"]) for r in recorded] == [
        (1, "csv", T1),
        (2, "csv", T2),
    ]
    # 77 giltiga poster men 75 salonger: dubbletter räknas en gång.
    assert [(r["salons"], r["gone"]) for r in recorded] == [(75, 0), (75, 0)]
    assert all(r["finished_at"] for r in recorded)


def test_fixtures_are_all_seen_in_the_latest_run(conn):
    """Samma fixtures två gånger: ingen salong försvinner, 48 ringbara som förut."""
    ingest(conn, CsvSource(FIXTURES).fetch(), now=T1)
    summary = ingest(conn, CsvSource(FIXTURES).fetch(), now=T2)
    assert summary.gone == 0
    assert conn.execute("SELECT count(*) FROM callable_salon").fetchone()[0] == 48


# --- Försvinner och kommer tillbaka (acceptanskriteriet i #23) ---------------


def test_salon_that_disappears_is_not_callable_and_comes_back(conn):
    ingest(conn, [SAX, KAMMEN], now=T1)
    assert callable_names(conn) == {"Saxen", "Kammen"}

    gone = ingest(conn, [SAX], now=T2)
    assert gone.gone == 1
    assert callable_names(conn) == {"Saxen"}
    # Raden ligger kvar: call_list_row pekar på den och historiken ska gå att läsa.
    assert conn.execute("SELECT count(*) FROM salon WHERE name = 'Kammen'").fetchone()[0] == 1

    back = ingest(conn, [SAX, KAMMEN], now=T3)
    assert (back.inserted, back.updated, back.gone) == (0, 2, 0)
    assert callable_names(conn) == {"Saxen", "Kammen"}
    row = conn.execute("SELECT * FROM salon WHERE name = 'Kammen'").fetchone()
    assert (row["first_seen_at"], row["last_seen_at"]) == (T1, T3)


def test_only_the_missing_workplace_disappears(conn):
    """Per arbetsställe, inte per orgnr som spärrlistan: att en salong stänger
    säger inget om företagets andra salonger."""
    solna = salon("Klipp Solna", "1")
    soder = salon("Klipp Söder", "2")
    ingest(conn, [solna, soder], now=T1)
    ingest(conn, [soder], now=T2)
    assert callable_names(conn) == {"Klipp Söder"}


def test_a_salon_missing_from_an_older_run_stays_gone(conn):
    """Bara den senaste körningen räknas, inte om salongen fanns någon gång."""
    ingest(conn, [SAX, KAMMEN], now=T1)
    ingest(conn, [SAX], now=T2)
    third = ingest(conn, [SAX], now=T3)
    assert third.gone == 0  # försvann redan i körning 2
    assert callable_names(conn) == {"Saxen"}


# --- Kraschade och tomma körningar sparas inte -------------------------------


def test_crashed_run_marks_nothing_gone(conn):
    ingest(conn, [SAX, KAMMEN], now=T1)

    def crashes_halfway():
        yield SAX
        raise RuntimeError("SCB svarar inte")

    with pytest.raises(RuntimeError):
        ingest(conn, crashes_halfway(), now=T2)

    assert [r["id"] for r in runs(conn)] == [1]
    assert callable_names(conn) == {"Saxen", "Kammen"}
    last_seen = {r["last_seen_at"] for r in conn.execute("SELECT last_seen_at FROM salon")}
    assert last_seen == {T1}  # inte ens Saxen, som hann skrivas, ändrades


@pytest.mark.parametrize(
    "delivered",
    [
        [],
        [RawSalon(orgnr="123", name="Felaktiga AB", cfar="1", **CALLABLE)],  # avvisas
    ],
    ids=["nothing", "only-rejected"],
)
def test_empty_run_marks_nothing_gone(conn, delivered):
    """Inga giltiga salonger är troligare ett fel hos källan än att alla har stängt."""
    ingest(conn, [SAX, KAMMEN], now=T1)
    with pytest.raises(EmptyRunError):
        ingest(conn, delivered, now=T2)
    assert [r["id"] for r in runs(conn)] == [1]
    assert callable_names(conn) == {"Saxen", "Kammen"}


# --- Fail closed ---------------------------------------------------------------


def test_salons_from_before_migration_009_wait_for_the_next_run(tmp_path, monkeypatch):
    """En befintlig databas (t.ex. reacher_scb.db) har salonger men ingen körning.
    Efter migrationen är de inte ringbara förrän nästa ingest har sett dem."""
    old_migrations = tmp_path / "old_migrations"
    old_migrations.mkdir()
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(sql_file.name.split("_", 1)[0]) < 9:
            shutil.copy(sql_file, old_migrations)

    with closing(connect(tmp_path / "t.db")) as conn:
        monkeypatch.setattr("reacher.db.MIGRATIONS_DIR", old_migrations)
        migrate(conn)
        fields = {"orgnr": "5561234567", "cfar": "1", "name": "Saxen", **CALLABLE}
        conn.execute(
            f"INSERT INTO salon ({', '.join(fields)}, first_seen_at, last_seen_at) "
            f"VALUES ({', '.join(':' + f for f in fields)}, :now, :now)",
            {**fields, "now": T1},
        )
        conn.commit()
        assert callable_names(conn) == {"Saxen"}

        monkeypatch.undo()
        assert migrate(conn) == [9]
        assert callable_names(conn) == set()

        ingest(conn, [SAX], now=T2)
        assert callable_names(conn) == {"Saxen"}


# --- CLI -----------------------------------------------------------------------


def test_cli_reports_gone_salons(tmp_path):
    db = str(tmp_path / "cli.db")
    result = CliRunner().invoke(app, ["load-seed", "--db", db])
    assert result.exit_code == 0, result.output
    assert "0 försvunna sedan förra körningen" in result.stdout


def test_cli_refuses_an_empty_run(tmp_path):
    db = tmp_path / "cli.db"
    runner = CliRunner()
    assert runner.invoke(app, ["load-seed", "--db", str(db)]).exit_code == 0

    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "salons.csv").write_text(",".join(CSV_COLUMNS) + "\n", encoding="utf-8")
    (empty / "signals.csv").write_text(",".join(SIGNAL_CSV_COLUMNS) + "\n", encoding="utf-8")
    result = runner.invoke(app, ["ingest", "--path", str(empty), "--db", str(db)])

    assert result.exit_code == 1
    assert "Inget sparades" in result.output
    with closing(connect(db)) as conn:
        assert conn.execute("SELECT count(*) FROM callable_salon").fetchone()[0] == 48
