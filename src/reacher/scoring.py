"""Poäng och klartextskäl per salong, ur salongens egna fakta (T2-01, T2-08).

Rena funktioner: ingen databas och ingen klocka. Anroparen skickar in dagens
datum, så att en lista byggd för en viss vecka ger samma poäng om den byggs om.
Vikter och gränser kommer från scoring.yaml, aldrig härifrån.

En ny signal = en inställningsmodell och en härledningsfunktion i SIGNALS, plus
ett block i scoring.yaml (D29). config.py bygger sin validering från SIGNALS, så
filen och registret måste innehålla samma signaler.

Signalrader med halveringstid (signal-tabellen) ingår inte i MVP:n (D4).
"""

import calendar
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from reacher.config import ScoringConfig

REGISTERED_RECENTLY = "registered_recently"
SMALL_EMPLOYER = "small_employer"

# Klartext för "Varför vi ringer". Okända koder får en generisk text i stället
# för att krascha, om någon lägger till en storleksklass i scoring.yaml.
EMPLOYEE_CLASS_TEXT = {"2": "1-4 anställda"}


class SignalSettings(BaseModel):
    """Det varje signalblock i scoring.yaml har. enabled: false = härleds inte alls;
    weight: 0 = skuggläge (D26): härleds och sparas, men inga poäng och inget skäl."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    # Inga minuspoäng: ingen signal i modellen talar emot en salong, och ett
    # tappat tecken ska inte kunna trycka ner salonger i listan.
    weight: float = Field(ge=0)


class RegisteredRecentlySettings(SignalSettings):
    # Registrerad för högst så här många kalendermånader sedan.
    months: int = Field(gt=0)


class SmallEmployerSettings(SignalSettings):
    # SCB-koder som text, precis som i salon.employee_class. En okvoterad 2 i
    # YAML blir en int och avvisas, eftersom den aldrig skulle matcha "2".
    classes: list[str] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class SalonFacts:
    """Det scoringen behöver veta om en salong. None = källan sa ingenting."""

    salon_id: int
    registered_at: date | None = None
    employee_class: str | None = None  # SCB-kod som text, t.ex. "2"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SalonFacts":
        """Från en rad ur callable_salon. Datum ligger som ISO-8601 TEXT i databasen."""
        registered_at = row["registered_at"]
        return cls(
            salon_id=row["id"],
            registered_at=date.fromisoformat(registered_at) if registered_at else None,
            employee_class=row["employee_class"],
        )


@dataclass(frozen=True, slots=True)
class Score:
    total: float
    reasons: tuple[str, ...]  # blir "Varför vi ringer" och call_list_row.reasons
    # Alla härledda signaler, även de med vikt 0. Blir call_list_row.signals, så
    # att T2-07 kan jämföra utfall med och utan en signal.
    signals: tuple[str, ...]


def months_before(day: date, months: int) -> date:
    """Samma dag `months` kalendermånader tidigare. 31 mars minus 1 -> 28/29 feb."""
    year, month0 = divmod(day.year * 12 + day.month - 1 - months, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def whole_months_between(start: date, end: date) -> int:
    """Antal hela kalendermånader från start till end."""
    months = (end.year - start.year) * 12 + end.month - start.month
    return months - 1 if end.day < start.day else months


def _registered_text(months: int) -> str:
    if months == 0:
        return "Registrerad för mindre än en månad sedan"
    if months == 1:
        return "Registrerad för 1 månad sedan"
    return f"Registrerad för {months} månader sedan"


def _registered_recently(
    facts: SalonFacts, settings: RegisteredRecentlySettings, today: date
) -> str | None:
    registered = facts.registered_at
    cutoff = months_before(today, settings.months)
    # Ett datum i framtiden är ett datafel, inte en nystartad salong.
    if registered is None or not cutoff <= registered <= today:
        return None
    return _registered_text(whole_months_between(registered, today))


def _small_employer(facts: SalonFacts, settings: SmallEmployerSettings, today: date) -> str | None:
    code = facts.employee_class
    if code is None or code not in settings.classes:
        return None
    return EMPLOYEE_CLASS_TEXT.get(code, f"Storleksklass {code} hos SCB")


@dataclass(frozen=True, slots=True)
class Signal:
    settings: type[SignalSettings]
    # (fakta, signalens inställningar, dagens datum) -> klartextskäl, eller None
    # om salongen inte har signalen. Saknad fakta ger None, aldrig ett undantag.
    derive: Callable[[SalonFacts, Any, date], str | None]


# Ordningen här är ordningen på skälen i "Varför vi ringer".
SIGNALS: dict[str, Signal] = {
    REGISTERED_RECENTLY: Signal(RegisteredRecentlySettings, _registered_recently),
    SMALL_EMPLOYER: Signal(SmallEmployerSettings, _small_employer),
}


def derive_signals(facts: SalonFacts, cfg: "ScoringConfig", today: date) -> dict[str, str]:
    """De påslagna signaler salongen uppfyller, som nyckel -> klartextskäl."""
    signals: dict[str, str] = {}
    for key, signal in SIGNALS.items():
        settings = cfg.signal(key)
        if not settings.enabled:
            continue
        reason = signal.derive(facts, settings, today)
        if reason is not None:
            signals[key] = reason
    return signals


def score_salon(facts: SalonFacts, cfg: "ScoringConfig", today: date) -> Score:
    """Summan av vikterna för salongens signaler, plus skälen i klartext."""
    signals = derive_signals(facts, cfg, today)
    total = 0.0
    reasons: list[str] = []
    for key, reason in signals.items():
        weight = cfg.signal(key).weight
        # Skuggläge (D26): signalen sparas i Score.signals, men en hypotes som
        # inte är bevisad ger inga poäng och inget skäl att visa säljaren.
        if weight == 0:
            continue
        total += weight
        reasons.append(reason)
    return Score(total=total, reasons=tuple(reasons), signals=tuple(signals))
