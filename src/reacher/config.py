from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, create_model

from reacher.scoring import SIGNALS, SignalSettings

# Ett obligatoriskt fält per signal i registret, med signalens egen modell.
# extra="forbid" gör att ett okänt namn (stavfel eller signal utan
# härledningsfunktion) stoppar inläsningen, och en signal som saknas i
# scoring.yaml gör det också: att stänga av en signal är alltid ett uttryckligt
# enabled: false, aldrig ett borttaget block (D29).
Signals = create_model(
    "Signals",
    __config__=ConfigDict(extra="forbid"),
    **{key: (signal.settings, ...) for key, signal in SIGNALS.items()},
)


class ScoringConfig(BaseModel):
    # extra="forbid" fångar stavfel i YAML. Utan den blir "signal:" tyst ignorerat
    # och alla får noll poäng, vilket är en otrevlig halvdag att felsöka.
    model_config = ConfigDict(extra="forbid")

    version: str
    half_life_days: float = Field(gt=0)
    signals: Signals

    def signal(self, key: str) -> SignalSettings:
        return getattr(self.signals, key)

    @classmethod
    def load(cls, path: Path = Path("scoring.yaml")) -> "ScoringConfig":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
