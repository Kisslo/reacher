import shutil
import sqlite3
from contextlib import closing

import pytest

from reacher.db import MIGRATIONS_DIR, connect, migrate

COMPLIANCE_COLUMNS = (
    "legal_form",
    "ftax_status",
    "vat_status",
    "employer_status",
    "company_status",
    "workplace_status",
    "ad_block_type",
    "phone_block_type",
    "workplace_ad_block_type",
    "workplace_phone_block_type",
)


def test_migrate_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    with closing(connect(db)) as conn:
        assert migrate(conn) == [1, 2, 3, 4, 5, 6, 7, 8]
    with closing(connect(db)) as conn:
        assert migrate(conn) == []  # andra körningen gör ingenting


def test_rows_built_before_t2_08_keep_signals_null(tmp_path, monkeypatch):
    """Migration 005 får inte påstå att gamla rader saknade signaler: NULL = okänt."""
    old_migrations = tmp_path / "old_migrations"
    old_migrations.mkdir()
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(sql_file.name.split("_", 1)[0]) < 5:
            shutil.copy(sql_file, old_migrations)

    db = tmp_path / "t.db"
    with closing(connect(db)) as conn:
        monkeypatch.setattr("reacher.db.MIGRATIONS_DIR", old_migrations)
        migrate(conn)
        conn.execute(
            "INSERT INTO salon (orgnr, name, first_seen_at, last_seen_at) "
            "VALUES ('5561234567', 'Klipp & Co', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO call_list (week, seller, scoring_version, created_at) "
            "VALUES ('2026w40', 'anna', 'v2', '2026-09-28')"
        )
        conn.execute(
            "INSERT INTO call_list_row (call_list_id, salon_id, rank, score, reasons) "
            "VALUES (1, 1, 1, 2, '[]')"
        )
        conn.commit()

        monkeypatch.undo()
        assert migrate(conn) == [5, 6, 7, 8]
        assert conn.execute("SELECT signals FROM call_list_row").fetchone()[0] is None


def test_orgnr_without_cfar_is_deduped(tmp_path):
    """Regressionstest för NULL-fällan i salon_identity."""
    with closing(connect(tmp_path / "t.db")) as conn:
        migrate(conn)
        ins = (
            "INSERT INTO salon (orgnr, name, first_seen_at, last_seen_at) "
            "VALUES ('5561234567', 'Klipp & Co', '2026-01-01', '2026-01-01')"
        )
        conn.execute(ins)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ins)


def test_opt_out_survives_removal_of_other_suppression_reason(tmp_path):
    """Ett orgnr kan ha flera spärrskäl; att ta bort ett får inte häva de andra."""
    with closing(connect(tmp_path / "t.db")) as conn:
        migrate(conn)
        ins = "INSERT INTO suppression (orgnr, reason, created_at) VALUES ('5561234567', ?, ?)"
        conn.execute(ins, ("existing_customer", "2026-01-01"))
        conn.execute(ins, ("opt_out", "2026-02-01"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ins, ("opt_out", "2026-03-01"))  # samma skäl två gånger

        conn.execute("DELETE FROM suppression WHERE reason = 'existing_customer'")
        blocked = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM suppression WHERE orgnr = '5561234567')"
        ).fetchone()[0]
        assert blocked == 1


def test_foreign_keys_are_enforced(tmp_path):
    """PRAGMA foreign_keys är av som standard i SQLite; connect() måste slå på den."""
    with closing(connect(tmp_path / "t.db")) as conn:
        migrate(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO contact (salon_id, kind, value, found_at) "
                "VALUES (999, 'phone', '+46701234567', '2026-01-01')"
            )


def test_compliance_fields_store_unknown_codes_raw(tmp_path):
    """D18: råa SCB-koder, TEXT och nullable, utan CHECK. En okänd kod ska
    lagras (och faila stängt i callable_salon), inte krascha ingest."""
    with closing(connect(tmp_path / "t.db")) as conn:
        migrate(conn)
        cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(salon)")}
        for col in COMPLIANCE_COLUMNS:
            assert cols[col]["type"] == "TEXT"
            assert cols[col]["notnull"] == 0

        conn.execute(
            "INSERT INTO salon "
            "(orgnr, name, first_seen_at, last_seen_at, ftax_status, ad_block_type) "
            "VALUES ('5561234567', 'Klipp & Co', '2026-01-01', '2026-01-01', '7', '5')"
        )
        row = conn.execute("SELECT * FROM salon").fetchone()
        assert row["ftax_status"] == "7"  # okänd kod, lagrad som den kom
        assert row["ad_block_type"] == "5"
        assert row["legal_form"] is None  # källan sa ingenting


def test_salon_has_no_area_column(tmp_path):
    """Område i Excel kommer från city. En area-kolumn utan källa ska inte komma tillbaka."""
    with closing(connect(tmp_path / "t.db")) as conn:
        migrate(conn)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(salon)")}
        assert "area" not in columns and "city" in columns


def test_old_reklam_columns_are_gone(tmp_path):
    """D32: den tvåsiffriga Reklam-koden finns inte i nya API:t. En kvarglömd
    kolumn utan källa skulle se ut som data men alltid vara NULL."""
    with closing(connect(tmp_path / "t.db")) as conn:
        migrate(conn)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(salon)")}
        assert not columns & {"ad_status", "workplace_ad_status"}
