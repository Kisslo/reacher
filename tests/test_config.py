"""Låser formen på scoring.yaml - inte värdena, som är till för att tunas."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from reacher.config import ScoringConfig
from reacher.scoring import SIGNALS

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_CONFIG = REPO_ROOT / "scoring.yaml"

SIGNAL_BLOCKS = {
    "registered_recently": {"enabled": True, "weight": 1, "months": 24},
    "small_employer": {"enabled": True, "weight": 1, "classes": ["2"]},
}
VALID = {"version": "v1", "half_life_days": 90, "signals": SIGNAL_BLOCKS}


def with_signal(key: str, **changes) -> dict:
    """VALID med ett ändrat signalblock. Värdet None tar bort nyckeln."""
    block = {**SIGNAL_BLOCKS[key], **changes}
    block = {name: value for name, value in block.items() if value is not None}
    return {**VALID, "signals": {**SIGNAL_BLOCKS, key: block}}


def test_committed_config_parses():
    """Den incheckade filen ska alltid gå att läsa. Fångar trasig YAML i en PR."""
    cfg = ScoringConfig.load(COMMITTED_CONFIG)
    assert cfg.version
    assert cfg.half_life_days > 0


def test_valid_payload_names_every_registered_signal():
    """Testerna nedan bygger på VALID. Ny signal i registret = nytt block här."""
    assert set(SIGNAL_BLOCKS) == set(SIGNALS)
    ScoringConfig.model_validate(VALID)


def test_signal_settings_are_read_per_signal():
    cfg = ScoringConfig.model_validate(with_signal("small_employer", weight=2.5, enabled=False))
    assert cfg.signal("small_employer").weight == 2.5
    assert cfg.signal("small_employer").enabled is False
    assert cfg.signal("registered_recently").months == 24


def test_unknown_signal_is_rejected():
    """Ett stavfel, eller en signal utan härledningsfunktion, ska explodera och
    inte tyst ge noll poäng (D29)."""
    for unknown in ("small_employers", "loss_making"):
        signals = {**SIGNAL_BLOCKS, unknown: {"enabled": True, "weight": 1}}
        with pytest.raises(ValidationError, match=unknown):
            ScoringConfig.model_validate({**VALID, "signals": signals})


@pytest.mark.parametrize("missing", sorted(SIGNALS))
def test_missing_signal_is_rejected(missing):
    """Att stänga av en signal är enabled: false, inte ett borttaget block."""
    signals = {key: block for key, block in SIGNAL_BLOCKS.items() if key != missing}
    with pytest.raises(ValidationError, match=missing):
        ScoringConfig.model_validate({**VALID, "signals": signals})


def test_old_v2_format_is_rejected():
    """weights/thresholds finns inte längre. En gammal fil ska inte halvfungera."""
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(
            {
                "version": "v2",
                "half_life_days": 90,
                "weights": {"registered_recently": 1, "small_employer": 1},
                "thresholds": {
                    "registered_recently_months": 24,
                    "small_employer_classes": ["2"],
                },
            }
        )


def test_typo_in_a_top_level_key_is_rejected():
    """Ett stavfel som signal: ska explodera, inte tyst ge noll poäng."""
    payload = {"version": "v1", "half_life_days": 90, "signal": SIGNAL_BLOCKS}
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(payload)


@pytest.mark.parametrize("bad", [0, -1, -90.5])
def test_half_life_must_be_positive(bad):
    """0 ger division med noll i decay-formeln, negativt får gamla signaler växa."""
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate({**VALID, "half_life_days": bad})


@pytest.mark.parametrize("missing", ["version", "half_life_days", "signals"])
def test_every_field_is_required(missing):
    payload = {k: v for k, v in VALID.items() if k != missing}
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("key", "setting"),
    [
        ("registered_recently", "enabled"),
        ("registered_recently", "weight"),
        ("registered_recently", "months"),
        ("small_employer", "enabled"),
        ("small_employer", "weight"),
        ("small_employer", "classes"),
    ],
)
def test_every_signal_setting_is_required(key, setting):
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(with_signal(key, **{setting: None}))


def test_weight_zero_is_allowed_for_shadow_mode():
    cfg = ScoringConfig.model_validate(with_signal("small_employer", weight=0))
    assert cfg.signal("small_employer").weight == 0


def test_negative_weight_is_rejected():
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(with_signal("small_employer", weight=-1))


@pytest.mark.parametrize("bad", [0, -24])
def test_registered_recently_months_must_be_positive(bad):
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(with_signal("registered_recently", months=bad))


def test_typo_in_a_signal_setting_is_rejected():
    payload = with_signal("registered_recently", months=None, month=24)
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(payload)


def test_setting_from_another_signal_is_rejected():
    """classes hör till small_employer. På fel signal är det ett misstag, inte en gräns."""
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(with_signal("registered_recently", classes=["2"]))


def test_size_class_codes_must_be_quoted_in_yaml():
    """employee_class är TEXT. Okvoterat [2] i YAML blir int och skulle aldrig matcha "2"."""
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(with_signal("small_employer", classes=[2]))


def test_config_survives_a_round_trip_through_yaml(tmp_path):
    """model_dump ska ge en fil som går att läsa in igen, med varje signals egna fält."""
    cfg = ScoringConfig.model_validate(with_signal("small_employer", weight=0))
    path = tmp_path / "scoring.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump()), encoding="utf-8")
    assert ScoringConfig.load(path) == cfg
