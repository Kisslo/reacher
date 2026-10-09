import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from reacher.sources.base import FinancialSource, SalonSource

if TYPE_CHECKING:
    from reacher.report import GroupStats

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


def _run_financial_ingest(source: FinancialSource, db: Path) -> None:
    """Som _run_ingest, för financial_fact (T1-12)."""
    from reacher.db import connect, migrate
    from reacher.ingest import ingest_financials

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    conn = connect(db)
    try:
        migrate(conn)
        summary = ingest_financials(conn, source.fetch())
    finally:
        conn.close()
    typer.echo(
        f"{db} ({source.name}): {summary.inserted} nya, {summary.updated} ändrade, "
        f"{summary.unchanged} oförändrade, {summary.rejected} avvisade finansiella fakta"
    )


@app.command("load-seed")
def load_seed(db: Path = DEFAULT_DB) -> None:
    """Läs in test-salongerna och deras finansiella fakta från tests/fixtures/."""
    from reacher.sources.csv_source import CsvFinancialSource, CsvSource

    _run_ingest(CsvSource(FIXTURES_DIR), db)
    _run_financial_ingest(CsvFinancialSource(FIXTURES_DIR), db)


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


@app.command("check-sources")
def check_sources(config: Path = Path("sources.yaml")) -> None:
    """Kontrollera sources.yaml och att API-nycklarna finns. Inga nätverksanrop."""
    from reacher.sources.config import API_KEYS, MissingApiKeyError, SourcesConfig, api_key

    cfg = SourcesConfig.load(config)
    typer.echo(
        f"{config}: SNI {', '.join(cfg.scb.sni_codes)}, "
        f"kommuner {', '.join(cfg.scb.municipalities)}"
    )
    missing = 0
    for name in API_KEYS:
        try:
            api_key(name)
        except MissingApiKeyError as error:
            typer.echo(str(error), err=True)
            missing += 1
        else:
            typer.echo(f"{name}: satt")  # aldrig värdet, inte ens en del av det (D24)
    if missing:
        raise typer.Exit(code=1)


# Så många företag i rad som får misslyckas innan körningen avbryts. Då är det
# Bolagsverket eller nätet som är nere, inte ett enskilt företag.
MAX_FAILURES_IN_A_ROW = 3


@app.command("fetch-financials")
def fetch_financials(db: Path = DEFAULT_DB, config: Path = Path("sources.yaml")) -> None:
    """Hämta omsättning och resultat från Bolagsverket för ringbara företag (T1-14)."""
    from reacher.db import connect, migrate
    from reacher.ingest import (
        FinancialSummary,
        financial_candidates,
        ingest_financials,
        stored_fiscal_years,
    )
    from reacher.sources.bolagsverket import (
        BolagsverketAuthError,
        BolagsverketClient,
        BolagsverketError,
        BolagsverketSource,
    )
    from reacher.sources.config import (
        BOLAGSVERKET_CLIENT_ID,
        BOLAGSVERKET_CLIENT_SECRET,
        MissingApiKeyError,
        SourcesConfig,
        api_key,
    )

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    settings = SourcesConfig.load(config).bolagsverket
    try:
        client = BolagsverketClient(
            api_key(BOLAGSVERKET_CLIENT_ID), api_key(BOLAGSVERKET_CLIENT_SECRET)
        )
    except MissingApiKeyError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None

    total = FinancialSummary()
    skipped = in_a_row = 0
    conn = connect(db)
    try:
        migrate(conn)
        candidates = financial_candidates(conn, settings.annual_report_legal_forms)
        source = BolagsverketSource(
            client, candidates, settings.tag_map, settings.years, stored_fiscal_years(conn)
        )
        for n, orgnr in enumerate(candidates, start=1):
            try:
                # En transaktion per företag: ett avbrott behåller det som redan är
                # hämtat, och nästa körning hämtar inte om de åren.
                summary = ingest_financials(conn, source.fetch_company(orgnr, n))
            except BolagsverketAuthError:
                raise
            except BolagsverketError as error:
                skipped += 1
                in_a_row += 1
                typer.echo(f"Företag {n} hoppas över: {error}", err=True)
                if in_a_row >= MAX_FAILURES_IN_A_ROW:
                    typer.echo(f"{in_a_row} företag i rad misslyckades, avbryter", err=True)
                    raise typer.Exit(code=1) from None
                continue
            in_a_row = 0
            total.inserted += summary.inserted
            total.updated += summary.updated
            total.unchanged += summary.unchanged
            total.rejected += summary.rejected
    except BolagsverketAuthError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    finally:
        conn.close()
    typer.echo(
        f"{db} ({source.name}): {len(candidates)} företag, {skipped} hoppades över. "
        f"{total.inserted} nya, {total.updated} ändrade, {total.unchanged} oförändrade, "
        f"{total.rejected} avvisade finansiella fakta"
    )


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
                        town=row.town,
                        address=row.address,
                        phone=row.phone,
                        source=row.source,
                        revenue=row.revenue,
                        result=row.result,
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


def _format_group(label: str, stats: "GroupStats") -> str:
    if stats.hit_rate is None:
        rate = "no reached calls"
    else:
        rate = f"{stats.hits}/{stats.reached} = {stats.hit_rate:.0%}"
    details = f"sent {stats.sent}, Ej nådd {stats.not_reached}, not called {stats.not_called}"
    if stats.unknown:
        details += f", unknown outcome {stats.unknown}"
    return f"  {label:<8}{rate:<20}({details})"


@app.command()
def report(week: str, db: Path = DEFAULT_DB) -> None:
    """Topp-20 mot resten (T2-06)."""
    from reacher.db import connect, migrate
    from reacher.report import TOP_N, ReportError, build_report

    conn = connect(db)
    try:
        migrate(conn)
        try:
            week_report = build_report(conn, week)
        except ReportError as error:
            raise typer.BadParameter(str(error), param_hint="week") from error
    finally:
        conn.close()

    top_label = f"Top {TOP_N}"
    typer.echo(f"Week {week}: top {TOP_N} = rank 1-{TOP_N} across all lists")
    typer.echo(
        "Hit rate = (Intresserad + Registrerad) / reached. "
        "Reached excludes Ej nådd and rows without an outcome."
    )
    for list_report in week_report.lists:
        typer.echo(f"{list_report.seller}:")
        typer.echo(_format_group(top_label, list_report.top))
        typer.echo(_format_group("Rest", list_report.rest))
    typer.echo("All lists:")
    typer.echo(_format_group(top_label, week_report.top))
    typer.echo(_format_group("Rest", week_report.rest))

    tie = week_report.boundary_tie
    if tie is not None:
        typer.echo(
            f"Note: {tie.in_top} rows in the top {TOP_N} and {tie.in_rest} in the rest share "
            f"score {tie.score:g}. Which side they are on is decided by tie-break, not score."
        )


if __name__ == "__main__":
    app()
