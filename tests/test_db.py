import sqlite3
from contextlib import closing

import pytest

from reacher.db import connect, migrate


def test_migrate_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    with closing(connect(db)) as conn:
        assert migrate(conn) == [1]
    with closing(connect(db)) as conn:
        assert migrate(conn) == []  # andra körningen gör ingenting


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
