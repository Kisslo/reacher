"""Anslutning och migrationer. Databasen är sanningen; Excel är in- och utdata."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def now() -> str:
    """ISO-8601 UTC. Använd den här överallt så tidsstämplar går att jämföra."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # per anslutning, inte per databas
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Kör omigrerade .sql-filer i ordning. Returnerar vilka som applicerades."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.commit()
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version")}

    ran = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(sql_file.name.split("_", 1)[0])
        if version in applied:
            continue
        # executescript committar implicit, så en krasch mitt i kan lämna
        # halvmigrerat tillstånd. Acceptabelt här: allt går att generera om
        # från fixtures - radera .db-filen och kör igen.
        conn.executescript(sql_file.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, now()),
        )
        conn.commit()
        ran.append(version)
    return ran
