import importlib
from contextlib import closing
from dataclasses import astuple
from datetime import date

import pytest
from typer.testing import CliRunner

from reacher.cli import FIXTURES_DIR, app
from reacher.config import ScoringConfig
from reacher.db import connect, migrate
from reacher.excel.contract import Outcome
from reacher.excel.export import CallListRow, export_call_list
from reacher.ingest import ingest
from reacher.lists import build_call_lists
from reacher.report import GroupStats, ReportError, build_report
from reacher.simulation import simulate_outcomes
from reacher.sources.csv_source import CsvSource

outcome_import = importlib.import_module("reacher.excel.import")

TODAY = date(2026, 9, 28)
WEEK = "2026w40"
CONFIG = ScoringConfig.model_validate(
    {
        "version": "test-v1",
        "half_life_days": 90,
        "signals": {
            "registered_recently": {"enabled": True, "weight": 1, "months": 24},
            "small_employer": {"enabled": True, "weight": 1, "classes": ["2"]},
        },
    }
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "report.db"


@pytest.fixture
def conn(db_path):
    with closing(connect(db_path)) as connection:
        migrate(connection)
        yield connection


def add_salons(conn, count, *, employee_class=None):
    for number in range(count):
        cursor = conn.execute(
            "INSERT INTO salon "
            "(orgnr, name, employee_class, legal_form, ftax_status, vat_status, "
            "employer_status, company_status, workplace_status, ad_block_type, "
            "phone_block_type, workplace_ad_block_type, workplace_phone_block_type, "
            "first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, '49', '1', '1', '1', '1', '1', '1', '1', '1', '1', "
            "'2026-09-28', '2026-09-28')",
            (f"orgnr-{employee_class}-{number}", f"Salon {number}", employee_class),
        )
        conn.execute(
            "INSERT INTO contact (salon_id, kind, value, found_at) "
            "VALUES (?, 'phone', ?, '2026-09-28')",
            (cursor.lastrowid, f"+46{cursor.lastrowid}"),
        )
    conn.commit()


def set_outcomes(conn, outcomes_by_rank):
    for rank, outcome in outcomes_by_rank.items():
        conn.execute(
            "INSERT INTO outcome (call_list_row_id, outcome, imported_at) "
            "SELECT call_list_row.id, ?, '2026-10-02' FROM call_list_row "
            "JOIN call_list ON call_list.id = call_list_row.call_list_id "
            "WHERE call_list.week = ? AND call_list_row.rank = ?",
            (outcome, WEEK, rank),
        )
    conn.commit()


@pytest.fixture
def two_lists(conn):
    add_salons(conn, 25)
    build_call_lists(conn, WEEK, ("anna", "bengt"), CONFIG, TODAY)
    set_outcomes(
        conn,
        {
            1: "Intresserad",
            2: "Registrerad",
            3: "Nej",
            4: "Spärra",
            5: "Ej nådd",
            21: "Nej",
            22: "Intresserad",
            25: "Ej nådd",
        },
    )
    return conn


def test_hit_rate_excludes_ej_nadd_and_rows_without_outcome(two_lists):
    report = build_report(two_lists, WEEK)

    assert report.top == GroupStats(sent=20, reached=4, hits=2, not_reached=1, not_called=15)
    assert report.rest == GroupStats(sent=5, reached=2, hits=1, not_reached=1, not_called=2)
    assert report.top.hit_rate == 0.5
    assert report.rest.hit_rate == 0.5


def test_per_list_split_uses_global_rank(two_lists):
    anna, bengt = build_report(two_lists, WEEK).lists

    # anna has the odd ranks 1-25, bengt the even ranks 2-24.
    assert (anna.seller, bengt.seller) == ("anna", "bengt")
    assert anna.top == GroupStats(sent=10, reached=2, hits=1, not_reached=1, not_called=7)
    assert anna.rest == GroupStats(sent=3, reached=1, hits=0, not_reached=1, not_called=1)
    assert bengt.top == GroupStats(sent=10, reached=2, hits=1, not_reached=0, not_called=8)
    assert bengt.rest == GroupStats(sent=2, reached=1, hits=1, not_reached=0, not_called=1)


def test_no_reached_calls_has_no_hit_rate(conn):
    add_salons(conn, 3)
    build_call_lists(conn, WEEK, ("anna",), CONFIG, TODAY)
    set_outcomes(conn, {1: "Ej nådd"})

    report = build_report(conn, WEEK)

    assert report.top.hit_rate is None
    assert report.rest == GroupStats()
    assert report.rest.hit_rate is None


def test_unknown_outcome_is_counted_but_not_reached(conn):
    add_salons(conn, 2)
    build_call_lists(conn, WEEK, ("anna",), CONFIG, TODAY)
    set_outcomes(conn, {1: "Kanske", 2: "Nej"})

    report = build_report(conn, WEEK)

    assert report.top == GroupStats(sent=2, reached=1, hits=0, unknown=1)


def test_other_weeks_are_not_counted(conn):
    add_salons(conn, 2)
    build_call_lists(conn, "2026w39", ("anna",), CONFIG, TODAY)
    build_call_lists(conn, WEEK, ("anna",), CONFIG, TODAY)
    conn.execute(
        "INSERT INTO outcome (call_list_row_id, outcome, imported_at) "
        "SELECT call_list_row.id, 'Intresserad', '2026-10-02' FROM call_list_row "
        "JOIN call_list ON call_list.id = call_list_row.call_list_id "
        "WHERE call_list.week = '2026w39'"
    )

    report = build_report(conn, WEEK)

    assert report.top == GroupStats(sent=2, not_called=2)


def test_unknown_week_is_rejected(conn):
    with pytest.raises(ReportError, match="2026w40"):
        build_report(conn, WEEK)


def test_tie_across_the_cut_is_reported(two_lists):
    tie = build_report(two_lists, WEEK).boundary_tie

    assert tie is not None
    assert (tie.score, tie.in_top, tie.in_rest) == (0, 20, 5)


def test_no_tie_when_the_cut_separates_scores(conn):
    add_salons(conn, 20, employee_class="2")
    add_salons(conn, 5)
    build_call_lists(conn, WEEK, ("anna",), CONFIG, TODAY)

    assert build_report(conn, WEEK).boundary_tie is None


def test_cli_prints_rates_with_sample_sizes(two_lists, db_path):
    result = CliRunner().invoke(app, ["report", WEEK, "--db", str(db_path)])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    all_lists = lines.index("All lists:")
    assert "2/4 = 50%" in lines[all_lists + 1]
    assert "sent 20, Ej nådd 1, not called 15" in lines[all_lists + 1]
    assert "1/2 = 50%" in lines[all_lists + 2]
    assert "anna:" in lines
    assert "share score 0" in result.stdout


def test_report_matches_simulated_and_imported_outcomes(conn, tmp_path):
    ingest(conn, CsvSource(FIXTURES_DIR).fetch(), now="2026-09-28T08:00:00+00:00")
    built = build_call_lists(conn, WEEK, ("anna", "bengt"), CONFIG, TODAY)
    for built_list in built:
        exported = tmp_path / f"{built_list.seller}.xlsx"
        simulated = tmp_path / f"{built_list.seller}_simulated.xlsx"
        export_call_list(
            exported,
            [
                CallListRow(
                    row.row_id,
                    row.rank,
                    row.score,
                    row.salon,
                    row.town,
                    row.phone,
                    row.source,
                    "; ".join(row.reasons),
                )
                for row in built_list.rows
            ],
            {
                "call_list_id": str(built_list.call_list_id),
                "week": WEEK,
                "seller": built_list.seller,
                "scoring_version": CONFIG.version,
                "generated_at": "2026-09-28T08:00:00+00:00",
            },
        )
        simulate_outcomes(conn, exported, simulated, FIXTURES_DIR / "ground_truth.csv", 42)
        outcome_import.import_outcomes(conn, simulated)

    report = build_report(conn, WEEK)
    imported = dict(conn.execute("SELECT outcome, count(*) FROM outcome GROUP BY outcome"))

    total = GroupStats(
        *(a + b for a, b in zip(astuple(report.top), astuple(report.rest), strict=True))
    )
    assert total.sent == sum(len(built_list.rows) for built_list in built)
    assert total.not_called == 0
    assert total.unknown == 0
    assert total.not_reached == imported.get(Outcome.EJ_NADD.value, 0)
    assert total.hits == sum(
        imported.get(outcome.value, 0) for outcome in (Outcome.INTRESSERAD, Outcome.REGISTRERAD)
    )
    assert report.top.sent == 20
    for group in ("top", "rest"):
        per_list = [astuple(getattr(list_report, group)) for list_report in report.lists]
        assert tuple(map(sum, zip(*per_list, strict=True))) == astuple(getattr(report, group))


def test_cli_rejects_unknown_week(conn, db_path):
    result = CliRunner().invoke(app, ["report", WEEK, "--db", str(db_path)])

    assert result.exit_code != 0
    assert "No call lists" in result.output
