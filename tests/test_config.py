"""Låser formen på scoring.yaml - inte värdena, som är till för att tunas."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from reacher.config import ScoringConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_CONFIG = REPO_ROOT / "scoring.yaml"

VALID = {"version": "v1", "half_life_days": 90, "weights": {"advertises_chair": 5}}


def test_committed_config_parses():
    """Den incheckade filen ska alltid gå att läsa. Fångar trasig YAML i en PR."""
    cfg = ScoringConfig.load(COMMITTED_CONFIG)
    assert cfg.version
    assert cfg.half_life_days > 0
    assert cfg.weights


def test_advertises_chair_is_weighted():
    """Enda signalen med riktigt bevis (citat från salongens sida). Försvinner inte."""
    cfg = ScoringConfig.load(COMMITTED_CONFIG)
    assert cfg.weights["advertises_chair"] > 0


def test_unknown_signal_keys_are_allowed():
    """weights är en öppen dict med flit - nya signaler ska inte kräva kodändring."""
    cfg = ScoringConfig.model_validate(
        {**VALID, "weights": {"helt_ny_signal": 2.5, "advertises_chair": 5}}
    )
    assert cfg.weights["helt_ny_signal"] == 2.5


def test_typo_in_a_top_level_key_is_rejected():
    """Ett stavfel som weigths ska explodera, inte tyst ge noll poäng."""
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(
            {"version": "v1", "half_life_days": 90, "weigths": {"advertises_chair": 5}}
        )


@pytest.mark.parametrize("bad", [0, -1, -90.5])
def test_half_life_must_be_positive(bad):
    """0 ger division med noll i decay-formeln, negativt får gamla signaler växa."""
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate({**VALID, "half_life_days": bad})


@pytest.mark.parametrize("missing", ["version", "half_life_days", "weights"])
def test_every_field_is_required(missing):
    payload = {k: v for k, v in VALID.items() if k != missing}
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(payload)
