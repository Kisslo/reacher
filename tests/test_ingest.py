"""T1-04: ingest från fixtures. Siffrorna nedan följer kantfallstabellen i
tests/fixtures/README.md: 70 poster, 3 ogiltiga orgnr, 2 dubbletter -> 65 salonger."""

import csv
from contextlib import closing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reacher.cli import app
from reacher.db import connect, migrate
from reacher.ingest import ingest
from reacher.sources.base import RawSalon
from reacher.sources.csv_source import CsvSource

FIXTURES = Path(__file__).parent / "fixtures"
T1 = "2026-10-01T08:00:00+00:00"
T2 = "2026-10-08T08:00:00+00:00"


@pytest.fixture
def conn(tmp_path):
    with closing(connect(tmp_path / "t.db")) as c:
        migrate(c)
        yield c


def load(conn, now=T1):
    return ingest(conn, CsvSource(FIXTURES).fetch(), now=now)


def snapshot(conn):
    """Allt i salon och contact utom last_seen_at, som ska ändras vid omkörning."""
    salons = [
        {k: r[k] for k in r.keys() if k != "last_seen_at"}
        for r in conn.execute("SELECT * FROM salon ORDER BY id")
    ]
    contacts = [dict(r) for r in conn.execute("SELECT * FROM contact ORDER BY id")]
    return salons, contacts


def phones_of(conn, orgnr):
    return sorted(
        r["value"]
        for r in conn.execute(
            "SELECT c.value FROM contact c JOIN salon s ON s.id = c.salon_id "
            "WHERE s.orgnr = ? AND c.kind = 'phone'",
            (orgnr,),
        )
    )


def test_first_run(conn):
    s = load(conn)
    assert (s.inserted, s.updated, s.rejected, s.invalid_phones) == (65, 2, 3, 2)


def test_second_run_gives_the_same_database(conn):
    load(conn, now=T1)
    before = snapshot(conn)
    s = load(conn, now=T2)
    assert (s.inserted, s.updated, s.rejected) == (0, 67, 3)
    assert snapshot(conn) == before


def test_first_seen_is_kept_and_last_seen_moves(conn):
    load(conn, now=T1)
    load(conn, now=T2)
    seen = {(r["first_seen_at"], r["last_seen_at"]) for r in conn.execute("SELECT * FROM salon")}
    assert seen == {(T1, T2)}


def test_salons_match_ground_truth(conn):
    """Samma nycklar som ground_truth.csv, som T1-03 skrev med normaliserat orgnr."""
    load(conn)
    with open(FIXTURES / "ground_truth.csv", encoding="utf-8", newline="") as f:
        truth = {(r["orgnr"], r["cfar"]) for r in csv.DictReader(f)}
    stored = {(r["orgnr"], r["cfar"] or "") for r in conn.execute("SELECT * FROM salon")}
    assert stored == truth


def test_invalid_orgnr_is_never_inserted(conn):
    load(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM salon")}
    assert not names & {"Felaktiga AB", "För Korta AB", "Namnlösa Salongen"}


def test_phones_are_e164(conn):
    load(conn)
    for (value,) in conn.execute("SELECT value FROM contact WHERE kind = 'phone'"):
        assert value.startswith("+46") and value[1:].isdigit()


def test_invalid_phone_keeps_the_salon(conn):
    load(conn)
    for orgnr in ("5560005067", "5560005075"):  # Klippet, Lockigt
        assert conn.execute("SELECT 1 FROM salon WHERE orgnr = ?", (orgnr,)).fetchone()
        assert phones_of(conn, orgnr) == []


def test_same_number_in_two_formats_is_one_row(conn):
    load(conn)
    assert phones_of(conn, "5560002023") == ["+46701740698"]  # Salong Dubbelgångaren


def test_duplicate_with_two_numbers_keeps_both(conn):
    """Hårateljén (rad 7-8) har två olika nummer. Båda sparas; build-lists (Team 2)
    måste välja ett. Ändras regeln ska det märkas här."""
    load(conn)
    assert phones_of(conn, "5560003039") == ["+46701740608", "+46701740609"]


def test_websites_are_stored_and_email_never(conn):
    load(conn)
    kinds = dict(conn.execute("SELECT kind, count(*) FROM contact GROUP BY kind").fetchall())
    assert kinds.get("website", 0) > 0
    assert "email" not in kinds


def test_missing_code_overwrites_old_code(conn):
    """Fail closed: säger källan inte längre något ska den gamla koden bort."""
    ingest(conn, [RawSalon(orgnr="5590001011", name="X", ad_status="11")], now=T1)
    ingest(conn, [RawSalon(orgnr="5590001011", name="X", ad_status=None)], now=T2)
    assert conn.execute("SELECT ad_status FROM salon").fetchone()[0] is None


def test_signal_without_salon_stops_the_source(tmp_path):
    (tmp_path / "salons.csv").write_text(
        (FIXTURES / "salons.csv").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "signals.csv").write_text(
        "orgnr,cfar,key,value,evidence,source_url,observed_at\n0000000000,,advertises_chair,,,,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="signaler utan salong"):
        list(CsvSource(tmp_path).fetch())


def test_load_seed_twice_via_cli(tmp_path):
    runner = CliRunner()
    db = str(tmp_path / "cli.db")
    first = runner.invoke(app, ["load-seed", "--db", db])
    second = runner.invoke(app, ["load-seed", "--db", db])
    assert first.exit_code == 0 and second.exit_code == 0
    assert "65 nya, 2 uppdaterade, 3 avvisade" in first.stdout
    assert "0 nya, 67 uppdaterade, 3 avvisade" in second.stdout


def test_ingest_csv_requires_path(tmp_path):
    result = CliRunner().invoke(app, ["ingest", "--db", str(tmp_path / "x.db")])
    assert result.exit_code != 0
