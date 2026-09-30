import logging
import sqlite3
from datetime import date
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
    from reacher.config import ScoringConfig
    from reacher.db import connect, migrate, now
    from reacher.excel.export import CallListRow, export_call_list
    from reacher.lists import build_call_lists as create_call_lists

    seller_names = tuple(seller.strip() for seller in sellers.split(",") if seller.strip())
    config = ScoringConfig.load()
    output_dir = Path("output")
    conn = connect(db)

    try:
        migrate(conn)
        built_lists = create_call_lists(conn, week, seller_names, config, date.today())

        for built_list in built_lists:
            output_path = output_dir / f"{week}_{built_list.seller}.xlsx"
            export_call_list(
                output_path,
                [
                    CallListRow(
                        row_id=row.row_id,
                        rank=row.rank,
                        score=row.score,
                        salon=row.salon,
                        area=row.area,
                        phone=row.phone,
                        source=row.source,
                        reasons="; ".join(row.reasons),
                    )
                    for row in built_list.rows
                ],
                {
                    "call_list_id": str(built_list.call_list_id),
                    "week": built_list.week,
                    "seller": built_list.seller,
                    "scoring_version": config.version,
                    "generated_at": now(),
                },
            )
            conn.execute(
                "UPDATE call_list SET file_path = ? WHERE id = ?",
                (str(output_path), built_list.call_list_id),
            )
            conn.commit()
            typer.echo(
                f"{built_list.seller}: {len(built_list.rows)} rows -> {output_path} "
                f"({built_list.skipped_without_phone} without phone skipped)"
            )
    except sqlite3.IntegrityError as error:
        raise typer.BadParameter(
            f"Could not create lists for week {week}; a seller/week may already exist: {error}"
        ) from error
    finally:
        conn.close()


@app.command("import-outcomes")
def import_outcomes(path: Path, db: Path = DEFAULT_DB) -> None:
    """Läs tillbaka utfall från en ifylld xlsx."""
    from importlib import import_module

    from reacher.db import connect, migrate

    outcome_import = import_module("reacher.excel.import")
    conn = connect(db)
    try:
        migrate(conn)
        try:
            summary = outcome_import.import_outcomes(conn, path)
        except outcome_import.OutcomeImportError as error:
            raise typer.BadParameter(str(error), param_hint="path") from error
    finally:
        conn.close()

    typer.echo(f"Imported outcomes: {summary.imported}")
    for outcome, count in summary.outcomes.items():
        typer.echo(f"  {outcome}: {count}")
    typer.echo(f"Empty outcomes: {summary.empty_outcomes}")
    typer.echo(f"Suppressions added: {sum(summary.suppressions_added.values())}")

    if summary.unknown_outcomes:
        typer.echo(f"Unknown outcomes: {', '.join(summary.unknown_outcomes)}")
    if summary.unknown_row_ids:
        typer.echo(f"Unknown row IDs: {', '.join(map(str, summary.unknown_row_ids))}")
    if summary.manual_review_row_ids:
        typer.echo(
            "Manual review required for comment warnings on row IDs: "
            + ", ".join(map(str, summary.manual_review_row_ids))
        )


@app.command("simulate-outcomes")
def simulate_outcomes_command(
    input_path: Path,
    output_path: Path,
    seed: int = 42,
    db: Path = DEFAULT_DB,
) -> None:
    """Fill a fixture call-list workbook with reproducible demo outcomes."""
    from reacher.db import connect, migrate
    from reacher.simulation import SimulationError, simulate_outcomes

    conn = connect(db)
    try:
        migrate(conn)
        try:
            counts = simulate_outcomes(
                conn,
                input_path,
                output_path,
                FIXTURES_DIR / "ground_truth.csv",
                seed,
            )
        except SimulationError as error:
            raise typer.BadParameter(str(error), param_hint="input_path") from error
    finally:
        conn.close()

    typer.echo(f"Simulated outcomes (seed {seed}) -> {output_path}")
    for outcome, count in counts.items():
        typer.echo(f"  {outcome}: {count}")


@app.command()
def report(week: str, db: Path = DEFAULT_DB) -> None:
    """Topp-20 mot resten."""
    raise NotImplementedError("E1")


if __name__ == "__main__":
    app()
