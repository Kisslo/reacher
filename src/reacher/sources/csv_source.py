"""CSV-källorna: läser salons.csv, signals.csv och financials.csv enligt kontraktet i base.py.

Normaliserar ingenting. Orgnr och telefon skickas vidare som de står i filen,
så att ingest städar CSV och SCB på samma sätt.
"""

import csv
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from reacher.sources.base import (
    CSV_COLUMNS,
    FINANCIAL_CSV_COLUMNS,
    SIGNAL_CSV_COLUMNS,
    RawFinancial,
    RawSalon,
    RawSignal,
)

Row = dict[str, str | None]


def _read(path: Path, columns: tuple[str, ...]) -> list[Row]:
    # newline="" krävs av csv-modulen, annars går citerade fält sönder.
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != columns:
            # Fångar t.ex. en fil som sparats om i Excel (semikolon i stället för komma).
            raise ValueError(f"{path.name}: rubrikraden följer inte kontraktet i base.py")
        # Tom cell betyder None (base.py).
        return [{k: (v if v != "" else None) for k, v in row.items()} for row in reader]


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class CsvSource:
    name = "csv"

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def fetch(self) -> Iterator[RawSalon]:
        salons = _read(self.directory / "salons.csv", CSV_COLUMNS)
        signals: dict[tuple[str | None, str | None], list[RawSignal]] = {}
        for row in _read(self.directory / "signals.csv", SIGNAL_CSV_COLUMNS):
            signals.setdefault((row["orgnr"], row["cfar"]), []).append(
                RawSignal(
                    key=row["key"] or "",
                    value=float(row["value"]) if row["value"] else 1.0,
                    evidence=row["evidence"],
                    source_url=row["source_url"],
                    observed_at=_date(row["observed_at"]),
                )
            )

        # Kontrollera innan något yield:as: en signal utan salong är ett fel i
        # fixturen och ska stoppa hela körningen, inte hoppas över (base.py).
        orphans = signals.keys() - {(r["orgnr"], r["cfar"]) for r in salons}
        if orphans:
            raise ValueError(f"signals.csv: signaler utan salong i salons.csv: {sorted(orphans)}")

        for row in salons:
            yield RawSalon(
                **{**row, "registered_at": _date(row["registered_at"])},
                signals=tuple(signals.get((row["orgnr"], row["cfar"]), ())),
            )


class CsvFinancialSource:
    """financials.csv (T1-12). Påhittade värden, samma form som Bolagsverket-adaptern
    (T1-14) kommer att leverera."""

    name = "csv-financials"

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def fetch(self) -> Iterator[RawFinancial]:
        rows = _read(self.directory / "financials.csv", FINANCIAL_CSV_COLUMNS)
        # Hela filen kontrolleras innan något yield:as, som för signals.csv.
        # Rad n = radnummer i filen (rubriken är rad 1).
        for n, row in enumerate(rows, start=2):
            if row["value"] is None or row["period_end"] is None:
                # Saknat värde = ingen rad (base.py). En rad med tom value är
                # ett fel i fixturen, inte ett sätt att skriva "saknas".
                raise ValueError(f"financials.csv rad {n}: value och period_end krävs")
        for row in rows:
            yield RawFinancial(
                orgnr=row["orgnr"] or "",
                period_end=date.fromisoformat(row["period_end"]),
                key=row["key"] or "",
                # int(), inte float(): "1234567.0" och "1 234 567" ska krascha här.
                value=int(row["value"]),
                source_document=row["source_document"],
            )
