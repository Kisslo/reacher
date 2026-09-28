"""Poäng och klartextskäl per salong, ur salongens egna fakta (T2-01).

Rena funktioner: ingen databas och ingen klocka. Anroparen skickar in dagens
datum, så att en lista byggd för en viss vecka ger samma poäng om den byggs om.
Vikter och gränser kommer från scoring.yaml, aldrig härifrån.

Signalrader med halveringstid (signal-tabellen) ingår inte i MVP:n (D4).
"""

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date

from reacher.config import ScoringConfig

REGISTERED_RECENTLY = "registered_recently"
SMALL_EMPLOYER = "small_employer"

# Klartext för "Varför vi ringer". Okända koder får en generisk text i stället
# för att krascha, om någon lägger till en storleksklass i scoring.yaml.
EMPLOYEE_CLASS_TEXT = {"2": "1-4 anställda"}


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


def derive_signals(facts: SalonFacts, cfg: ScoringConfig, today: date) -> dict[str, str]:
    """Signalerna salongen uppfyller, som nyckel -> klartextskäl. Saknad fakta ger ingen signal."""
    signals: dict[str, str] = {}
    limits = cfg.thresholds

    registered = facts.registered_at
    cutoff = months_before(today, limits.registered_recently_months)
    # Ett datum i framtiden är ett datafel, inte en nystartad salong.
    if registered is not None and cutoff <= registered <= today:
        signals[REGISTERED_RECENTLY] = _registered_text(whole_months_between(registered, today))

    code = facts.employee_class
    if code is not None and code in limits.small_employer_classes:
        signals[SMALL_EMPLOYER] = EMPLOYEE_CLASS_TEXT.get(code, f"Storleksklass {code} hos SCB")

    return signals


def score_salon(facts: SalonFacts, cfg: ScoringConfig, today: date) -> Score:
    """Summan av vikterna för salongens signaler, plus skälen i klartext."""
    total = 0.0
    reasons: list[str] = []
    for key, reason in derive_signals(facts, cfg, today).items():
        # En signal utan vikt är en borttagen hypotes: inga poäng, och inget
        # skäl att visa säljaren.
        weight = cfg.weights.get(key, 0.0)
        if weight == 0:
            continue
        total += weight
        reasons.append(reason)
    return Score(total=total, reasons=tuple(reasons))
