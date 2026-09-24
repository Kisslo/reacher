"""Gränssnittet mot alla datakällor. Inga implementationer här.

Fixtures (A1 skriver, A3 läser) är två CSV-filer med rubrikrad:
- salons.csv har exakt kolumnerna i CSV_COLUMNS, i den ordningen.
- signals.csv har exakt kolumnerna i SIGNAL_CSV_COLUMNS, en rad per signal.
  (orgnr, cfar) pekar på en rad i salons.csv. En signal utan matchande salong
  är ett fel i fixturen och ska avvisas, inte hoppas över.

Format i båda:
- UTF-8, komma som avgränsare.
- Tom cell betyder None. Inga "NULL" eller "-". Tom cfar matchar tom cfar.
- Datum som ÅÅÅÅ-MM-DD.
- orgnr och phone som de står i källan. Normalisering sker vid ingest (A4/A5),
  så att fixtures kan innehålla samma röra som riktig registerdata (A6).
- Ground truth (has_empty_chairs) finns aldrig i någon av filerna - se A2.
"""

from collections.abc import Iterator
from dataclasses import dataclass, fields
from datetime import date
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RawSignal:
    """En observation om en salong. Speglar tabellen signal."""

    key: str
    value: float = 1.0
    evidence: str | None = None  # citatet, ordagrant - blir "Varför vi ringer"
    source_url: str | None = None
    observed_at: date | None = None  # None -> ingest sätter dagens datum


@dataclass(frozen=True, slots=True)
class RawSalon:
    """Som källan levererar den - ovaliderad, onormaliserad."""

    orgnr: str
    name: str
    cfar: str | None = None
    sni: str | None = None
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    municipality: str | None = None
    area: str | None = None
    employee_class: str | None = None
    registered_at: date | None = None
    website: str | None = None
    phone: str | None = None
    # MVP: bara fixture-källan fyller i den här. Riktiga signaler kommer från
    # andra källor än salongerna (webbläsaren, Places) och får då en egen
    # SignalSource. RawSignal följer med oförändrad när det händer.
    signals: tuple[RawSignal, ...] = ()


CSV_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(RawSalon) if f.name != "signals")
SIGNAL_CSV_COLUMNS: tuple[str, ...] = ("orgnr", "cfar") + tuple(f.name for f in fields(RawSignal))


# runtime_checkable gör att testerna kan köra isinstance(källa, SalonSource).
# Den kollar bara att name och fetch finns, inte deras typer.
@runtime_checkable
class SalonSource(Protocol):
    name: str

    def fetch(self) -> Iterator[RawSalon]:
        """Yield:a salonger. Får vara lat - SCB paginerar 2000 rader per anrop."""
        ...
