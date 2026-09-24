from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ScoringConfig(BaseModel):
    # extra="forbid" fångar stavfel i YAML. Utan den blir "weigths:" tyst ignorerat
    # och alla får noll poäng, vilket är en otrevlig halvdag att felsöka.
    model_config = ConfigDict(extra="forbid")

    version: str
    half_life_days: float = Field(gt=0)
    weights: dict[str, float]

    @classmethod
    def load(cls, path: Path = Path("scoring.yaml")) -> "ScoringConfig":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
