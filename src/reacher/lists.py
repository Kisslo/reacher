"""Build and persist ranked call-list snapshots (T2-02)."""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from reacher.config import ScoringConfig
from reacher.db import now
from reacher.scoring import SalonFacts, Score, score_salon


@dataclass(frozen=True, slots=True)
class RankedSalon:
    """A scored callable salon before it is assigned to a salesperson."""

    salon_id: int
    name: str
    area: str
    phone: str
    source: str
    score: Score


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    """The values written to one frozen ``call_list_row`` record."""

    row_id: int
    salon_id: int
    rank: int
    score: float
    reasons: tuple[str, ...]
    signals: tuple[str, ...]
    phone: str
    salon: str
    area: str
    source: str


@dataclass(frozen=True, slots=True)
class BuiltList:
    """One persisted salesperson list and its frozen rows."""

    call_list_id: int
    week: str
    seller: str
    rows: tuple[SnapshotRow, ...]
    skipped_without_phone: int


def _rank_callable_salons(
    rows: Sequence[sqlite3.Row],
    config: ScoringConfig,
    built_on: date,
) -> tuple[list[RankedSalon], int]:
    ranked: list[RankedSalon] = []
    skipped_without_phone = 0

    for row in rows:
        phone = row["phone"]
        if not phone:
            skipped_without_phone += 1
            continue

        facts = SalonFacts.from_row(row)
        ranked.append(
            RankedSalon(
                salon_id=facts.salon_id,
                name=row["name"],
                area=row["city"] or "",
                phone=phone,
                source=row["phone_source_url"] or "",
                score=score_salon(facts, config, built_on),
            )
        )

    ranked.sort(key=lambda salon: (-salon.score.total, salon.salon_id))
    return ranked, skipped_without_phone


def build_call_lists(
    conn: sqlite3.Connection,
    week: str,
    sellers: Sequence[str],
    config: ScoringConfig,
    built_on: date,
) -> tuple[BuiltList, ...]:
    """Rank callable salons, split them by rank, and persist frozen snapshots.

    ``callable_salon`` is the eligibility source queried here. Migration 004
    exposes ``salon.*``; phone details are selected from the first phone
    contact for each eligible salon.
    """
    normalized_sellers = tuple(seller.strip() for seller in sellers if seller.strip())
    if not normalized_sellers:
        raise ValueError("At least one salesperson is required")
    if len(set(normalized_sellers)) != len(normalized_sellers):
        raise ValueError("Salespeople must be unique")

    callable_rows = conn.execute(
        "SELECT callable.id, callable.name, callable.city, callable.registered_at, "
        "callable.employee_class, "
        "(SELECT value FROM contact WHERE salon_id = callable.id AND kind = 'phone' "
        "ORDER BY id LIMIT 1) AS phone, "
        "(SELECT source_url FROM contact WHERE salon_id = callable.id AND kind = 'phone' "
        "ORDER BY id LIMIT 1) AS phone_source_url "
        "FROM callable_salon AS callable ORDER BY callable.id"
    ).fetchall()
    ranked, skipped_without_phone = _rank_callable_salons(callable_rows, config, built_on)
    created_at = now()
    built: list[BuiltList] = []

    with conn:
        for seller_index, seller in enumerate(normalized_sellers):
            cursor = conn.execute(
                "INSERT INTO call_list "
                "(week, seller, scoring_version, created_at) VALUES (?, ?, ?, ?)",
                (week, seller, config.version, created_at),
            )
            call_list_id = cursor.lastrowid
            snapshot_rows: list[SnapshotRow] = []

            for rank, salon in enumerate(ranked, start=1):
                if (rank - 1) % len(normalized_sellers) != seller_index:
                    continue

                cursor = conn.execute(
                    "INSERT INTO call_list_row "
                    "(call_list_id, salon_id, rank, score, reasons, signals, phone) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        call_list_id,
                        salon.salon_id,
                        rank,
                        salon.score.total,
                        json.dumps(salon.score.reasons, ensure_ascii=False),
                        json.dumps(salon.score.signals),
                        salon.phone,
                    ),
                )
                snapshot_rows.append(
                    SnapshotRow(
                        row_id=cursor.lastrowid,
                        salon_id=salon.salon_id,
                        rank=rank,
                        score=salon.score.total,
                        reasons=salon.score.reasons,
                        signals=salon.score.signals,
                        phone=salon.phone,
                        salon=salon.name,
                        area=salon.area,
                        source=salon.source,
                    )
                )

            built.append(
                BuiltList(
                    call_list_id=call_list_id,
                    week=week,
                    seller=seller,
                    rows=tuple(snapshot_rows),
                    skipped_without_phone=skipped_without_phone,
                )
            )

    return tuple(built)
