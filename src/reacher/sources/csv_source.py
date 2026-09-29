"""CSV-källan: läser salons.csv och signals.csv enligt kontraktet i base.py.

Normaliserar ingenting. Orgnr och telefon skickas vidare som de står i filen,
så att ingest städar CSV och SCB på samma sätt.
"""

import csv
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from reacher.sources.base import CSV_COLUMNS, SIGNAL_CSV_COLUMNS, RawSalon, RawSignal

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
