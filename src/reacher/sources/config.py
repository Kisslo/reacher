"""Källkonfiguration (sources.yaml) och API-nycklar från miljön (T1-11).

sources.yaml är incheckad och innehåller det som förväntas ändras: SNI-koder,
kommuner, juridiska former med årsredovisning och iXBRL-taggar (D29). Nycklar hör
aldrig hemma där. De ligger i varje utvecklares .env och läses från miljön vid
körning (D24): uv run --env-file .env reacher ...
"""

import os
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

# Koder är text, precis som i databasen. YAML gör okvoterade siffror till tal, och
# 0114 blir dessutom 76 (oktalt). Ett tal ska därför stoppa inläsningen, inte
# tolkas. [0-9] i stället för \d: pydantics regex räknar även andra skrifters siffror.
SniCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{5}$")]
MunicipalityCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}$")]
LegalFormCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{2}$")]
IxbrlTag = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][\w-]*:[A-Za-z]\w*$")]
FactKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]

# Alla nycklar som koden får läsa. Varje namn ska också stå i .env.example
# (testas). Bolagsverkets namn läggs till här och där med T1-14.
SCB_API_KEY = "SCB_API_KEY"
API_KEYS: tuple[str, ...] = (SCB_API_KEY,)


class ScbSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # SNI 2025 som API:t skriver dem: fem siffror utan punkt (docs/scb-fields.md).
    sni_codes: list[SniCode] = Field(min_length=1)
    # Arbetsställets kommun (belagenhetsadress.kommun), aldrig kommunSate (D19).
    municipalities: list[MunicipalityCode] = Field(min_length=1)


class BolagsverketSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bara de här juridiska formerna får årsredovisningar hämtade (D25).
    annual_report_legal_forms: list[LegalFormCode] = Field(min_length=1)
    # Hur många av de senaste räkenskapsåren som hämtas (D25, D27).
    years: int = Field(gt=0)
    # iXBRL-tagg -> nyckel i financial_fact. Nytt fält = ny rad, ingen migration (D29).
    tag_map: dict[IxbrlTag, FactKey] = Field(min_length=1)


class SourcesConfig(BaseModel):
    # extra="forbid" som i ScoringConfig: ett stavfel som "municipality:" ska
    # stoppa inläsningen, inte tyst ge en tom hämtning.
    model_config = ConfigDict(extra="forbid")

    scb: ScbSettings
    bolagsverket: BolagsverketSettings

    @classmethod
    def load(cls, path: Path = Path("sources.yaml")) -> "SourcesConfig":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class MissingApiKeyError(RuntimeError):
    """En nyckel saknas i miljön. Meddelandet innehåller bara variabelns namn."""


def api_key(name: str) -> SecretStr:
    """Läs en API-nyckel från miljön.

    SecretStr skrivs ut som '**********' i str(), repr(), loggar och tracebacks, så
    en nyckel som hamnar i ett felmeddelande läcker inte. Råvärdet hämtas med
    get_secret_value() på ett enda ställe: där adaptern bygger sin header.
    """
    if name not in API_KEYS:
        # Fångar stavfel i koden, och håller API_KEYS och .env.example kompletta.
        raise ValueError(f"okänd nyckel: {name}. Lägg till den i API_KEYS och .env.example")
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingApiKeyError(
            f"{name} saknas. Kopiera .env.example till .env, fyll i nyckeln och kör "
            "med: uv run --env-file .env reacher ..."
        )
    return SecretStr(value)
