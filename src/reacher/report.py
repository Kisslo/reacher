"""Weekly report: hit rate of the top 20 versus the rest (T2-06)."""

import sqlite3
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from reacher.excel.contract import Outcome

TOP_N = 20

HITS = frozenset({Outcome.INTRESSERAD.value, Outcome.REGISTRERAD.value})
# D23: reached = someone answered. "Ej nådd" and rows without an outcome say
# nothing about empty chairs, so they stay out of the denominator.
REACHED = HITS | {Outcome.NEJ.value, Outcome.SPARRA.value}


class ReportError(ValueError):
    """The requested week cannot be reported."""


@dataclass(frozen=True, slots=True)
class GroupStats:
    """Outcome counts for one group of frozen call-list rows."""

    sent: int = 0
    reached: int = 0
    hits: int = 0
    not_reached: int = 0
    not_called: int = 0
    unknown: int = 0

    @property
    def hit_rate(self) -> float | None:
        return self.hits / self.reached if self.reached else None


@dataclass(frozen=True, slots=True)
class ListReport:
    """Top 20 versus the rest within one salesperson's list."""

    call_list_id: int
    seller: str
    top: GroupStats
    rest: GroupStats


@dataclass(frozen=True, slots=True)
class BoundaryTie:
    """Rows sharing the score at rank ``TOP_N``: only the tie-break puts them on either side."""

    score: float
    in_top: int
    in_rest: int


@dataclass(frozen=True, slots=True)
class WeekReport:
    week: str
    lists: tuple[ListReport, ...]
    top: GroupStats
    rest: GroupStats
    boundary_tie: BoundaryTie | None


def _tally(outcomes: Iterable[str | None]) -> GroupStats:
    counts: Counter[str] = Counter()
    for outcome in outcomes:
        counts["sent"] += 1
        if outcome is None:
            counts["not_called"] += 1
        elif outcome == Outcome.EJ_NADD:
            counts["not_reached"] += 1
        elif outcome in REACHED:
            counts["reached"] += 1
            if outcome in HITS:
                counts["hits"] += 1
        else:
            counts["unknown"] += 1
    return GroupStats(**counts)


def _split(rows: Sequence[sqlite3.Row]) -> tuple[GroupStats, GroupStats]:
    top = _tally(row["outcome"] for row in rows if row["rank"] <= TOP_N)
    rest = _tally(row["outcome"] for row in rows if row["rank"] > TOP_N)
    return top, rest


def _boundary_tie(rows: Sequence[sqlite3.Row]) -> BoundaryTie | None:
    cut_score = next((row["score"] for row in rows if row["rank"] == TOP_N), None)
    if cut_score is None:
        return None
    tied = [row for row in rows if row["score"] == cut_score]
    in_top = sum(1 for row in tied if row["rank"] <= TOP_N)
    in_rest = len(tied) - in_top
    return BoundaryTie(cut_score, in_top, in_rest) if in_rest else None


def build_report(conn: sqlite3.Connection, week: str) -> WeekReport:
    """Compare the top 20 with the rest for every call list of ``week``.

    ``call_list_row.rank`` is the global rank across the week's lists, so the
    top 20 is the same 20 salons whether counted per list or combined.
    """
    call_lists = conn.execute(
        "SELECT id, seller FROM call_list WHERE week = ? ORDER BY id", (week,)
    ).fetchall()
    if not call_lists:
        raise ReportError(f"No call lists for week {week!r}")

    rows = conn.execute(
        "SELECT call_list_row.call_list_id, call_list_row.rank, call_list_row.score, "
        "outcome.outcome "
        "FROM call_list_row "
        "JOIN call_list ON call_list.id = call_list_row.call_list_id "
        "LEFT JOIN outcome ON outcome.call_list_row_id = call_list_row.id "
        "WHERE call_list.week = ? "
        "ORDER BY call_list_row.rank",
        (week,),
    ).fetchall()

    list_reports = []
    for call_list in call_lists:
        list_rows = [row for row in rows if row["call_list_id"] == call_list["id"]]
        top, rest = _split(list_rows)
        list_reports.append(ListReport(call_list["id"], call_list["seller"], top, rest))

    top, rest = _split(rows)
    return WeekReport(week, tuple(list_reports), top, rest, _boundary_tie(rows))
