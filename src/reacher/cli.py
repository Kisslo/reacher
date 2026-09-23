from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)

DEFAULT_DB = Path("reacher.db")


@app.command("init-db")
def init_db(db: Path = DEFAULT_DB) -> None:
    """Skapa databasen och kör migrationer."""
    from reacher.db import connect, migrate

    conn = connect(db)
    ran = migrate(conn)
    conn.close()
    typer.echo(f"{db}: applicerade migrationer {ran}" if ran else f"{db}: redan aktuell")


@app.command("load-seed")
def load_seed(db: Path = DEFAULT_DB) -> None:
    """Läs in test-salongerna från tests/fixtures/."""
    raise NotImplementedError("F6")


@app.command()
def ingest(source: str = "csv", db: Path = DEFAULT_DB) -> None:
    """Hämta salonger från en källa och upserta dem."""
    raise NotImplementedError("A4")


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
