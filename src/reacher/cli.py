import logging
from pathlib import Path

import typer

from reacher.sources.base import SalonSource

app = typer.Typer(no_args_is_help=True, add_completion=False)

DEFAULT_DB = Path("reacher.db")
# src/reacher/cli.py -> reporoten. Fungerar eftersom uv installerar paketet
# editerbart; load-seed är ett utvecklarkommando och körs alltid från repot.
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


@app.command("init-db")
def init_db(db: Path = DEFAULT_DB) -> None:
    """Skapa databasen och kör migrationer."""
    from reacher.db import connect, migrate

    conn = connect(db)
    ran = migrate(conn)
    conn.close()
    typer.echo(f"{db}: applicerade migrationer {ran}" if ran else f"{db}: redan aktuell")


def _run_ingest(source: SalonSource, db: Path) -> None:
    """Gemensamt för ingest och load-seed: migrera, läs in, skriv en sammanfattning."""
    from reacher.db import connect, migrate
    from reacher.ingest import ingest as run_ingest

    # Varningar (avvisade poster, ogiltiga nummer) går till stderr.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    conn = connect(db)
    try:
        migrate(conn)  # så att en tom .db-fil fungerar direkt
        summary = run_ingest(conn, source.fetch())
    finally:
        conn.close()
    typer.echo(
        f"{db} ({source.name}): {summary.inserted} nya, {summary.updated} uppdaterade, "
        f"{summary.rejected} avvisade, {summary.invalid_phones} ogiltiga telefonnummer"
    )


@app.command("load-seed")
def load_seed(db: Path = DEFAULT_DB) -> None:
    """Läs in test-salongerna från tests/fixtures/."""
    from reacher.sources.csv_source import CsvSource

    _run_ingest(CsvSource(FIXTURES_DIR), db)


@app.command()
def ingest(
    source: str = "csv",
    path: Path | None = None,  # mapp med salons.csv och signals.csv
    db: Path = DEFAULT_DB,
) -> None:
    """Hämta salonger från en källa och upserta dem."""
    if source == "csv":
        from reacher.sources.csv_source import CsvSource

        if path is None:
            raise typer.BadParameter("csv-källan kräver --path", param_hint="--path")
        _run_ingest(CsvSource(path), db)
    elif source == "scb":
        from reacher.sources.scb import ScbApiSource

        _run_ingest(ScbApiSource(), db)  # kraschar tills T1-06 (#22) finns
    else:
        raise typer.BadParameter(f"okänd källa: {source}", param_hint="--source")


@app.command("build-lists")
def build_lists(week: str, sellers: str, db: Path = DEFAULT_DB) -> None:
    """Poängsätt, filtrera, rangordna och skriv en xlsx per säljare."""
    raise NotImplementedError("B4 + C1")


@app.command("import-outcomes")
def import_outcomes(path: Path, db: Path = DEFAULT_DB) -> None:
    """Läs tillbaka utfall från en ifylld xlsx."""
    raise NotImplementedError("D1")


@app.command()
def report(week: str, db: Path = DEFAULT_DB) -> None:
    """Topp-20 mot resten."""
    raise NotImplementedError("E1")


if __name__ == "__main__":
    app()
