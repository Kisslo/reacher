from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Thresholds(BaseModel):
    """Gränserna för signalerna Team 2 härleder ur salongens fakta (D6)."""

    model_config = ConfigDict(extra="forbid")

    registered_recently_months: int = Field(gt=0)
    # SCB-koder som text, precis som i salon.employee_class. En okvoterad 2 i
    # YAML blir en int och avvisas, eftersom den aldrig skulle matcha "2".
    small_employer_classes: list[str] = Field(min_length=1)


class ScoringConfig(BaseModel):
    # extra="forbid" fångar stavfel i YAML. Utan den blir "weigths:" tyst ignorerat
    # och alla får noll poäng, vilket är en otrevlig halvdag att felsöka.
    model_config = ConfigDict(extra="forbid")

    version: str
    half_life_days: float = Field(gt=0)
    weights: dict[str, float]
    thresholds: Thresholds

    @classmethod
    def load(cls, path: Path = Path("scoring.yaml")) -> "ScoringConfig":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
