"""T1-11: sources.yaml och lokala API-nycklar. Inga nätverksanrop."""

import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from reacher.cli import app
from reacher.sources.config import (
    API_KEYS,
    SCB_API_KEY,
    MissingApiKeyError,
    SourcesConfig,
    api_key,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_CONFIG = REPO_ROOT / "sources.yaml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Påhittad. Ser ut som en nyckel så att ett läckage syns i assert-meddelandet.
FAKE_KEY = "test-0000-not-a-real-key"

SCB = {"sni_codes": ["96210", "96220"], "municipalities": ["0180"]}
BOLAGSVERKET = {
    "annual_report_legal_forms": ["49"],
    "years": 3,
    "tag_map": {"se-gen-base:Nettoomsattning": "revenue"},
}
VALID = {"scb": SCB, "bolagsverket": BOLAGSVERKET}

runner = CliRunner()


def with_scb(**changes) -> dict:
    return {**VALID, "scb": {**SCB, **changes}}


def with_bolagsverket(**changes) -> dict:
    return {**VALID, "bolagsverket": {**BOLAGSVERKET, **changes}}


def test_committed_config_parses():
    """Den incheckade filen ska alltid gå att läsa. Fångar trasig YAML i en PR."""
    cfg = SourcesConfig.load(COMMITTED_CONFIG)
    assert set(cfg.scb.sni_codes) == {"96210", "96220"}
    assert cfg.scb.municipalities
    assert cfg.bolagsverket.years == 3
    assert set(cfg.bolagsverket.tag_map.values()) >= {"revenue", "net_result"}


def test_valid_payload_parses():
    cfg = SourcesConfig.model_validate(VALID)
    assert cfg.scb.municipalities == ["0180"]
    assert cfg.bolagsverket.tag_map == {"se-gen-base:Nettoomsattning": "revenue"}


@pytest.mark.parametrize(
    "payload",
    [
        {**VALID, "sbc": SCB},  # stavfel på toppnivå
        with_scb(municipality=["0180"]),  # stavfel i scb
        with_bolagsverket(year=3),  # stavfel i bolagsverket
        {**VALID, "scb_api_key": FAKE_KEY},  # nycklar hör inte hemma i config
    ],
)
def test_unknown_key_is_rejected(payload):
    """extra="forbid": ett stavfel ska explodera, inte tyst ge en tom hämtning (D29)."""
    with pytest.raises(ValidationError):
        SourcesConfig.model_validate(payload)


@pytest.mark.parametrize("missing", ["scb", "bolagsverket"])
def test_every_section_is_required(missing):
    with pytest.raises(ValidationError):
        SourcesConfig.model_validate({k: v for k, v in VALID.items() if k != missing})


def test_unquoted_municipality_code_is_rejected():
    """YAML läser 0114 som det oktala talet 76. Det ska stoppa, inte bli kommun "76"."""
    payload = yaml.safe_load("municipalities: [0114]")
    assert payload["municipalities"] == [76]
    with pytest.raises(ValidationError):
        SourcesConfig.model_validate(with_scb(**payload))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("sni_codes", [96210]),  # tal, inte text
        ("sni_codes", ["96.210"]),  # fixturformatet, inte API:ets
        ("sni_codes", []),
        ("municipalities", ["180"]),
        ("municipalities", ["Stockholm"]),
        ("municipalities", []),
    ],
)
def test_bad_scb_codes_are_rejected(field, bad):
    with pytest.raises(ValidationError):
        SourcesConfig.model_validate(with_scb(**{field: bad}))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("annual_report_legal_forms", [49]),
        ("annual_report_legal_forms", ["AB"]),
        ("annual_report_legal_forms", []),
        ("years", 0),
        ("years", -3),
        ("tag_map", {}),
        ("tag_map", {"Nettoomsattning": "revenue"}),  # utan prefix
        ("tag_map", {"se-gen-base:Nettoomsattning": "Revenue"}),  # inte snake_case
    ],
)
def test_bad_bolagsverket_settings_are_rejected(field, bad):
    with pytest.raises(ValidationError):
        SourcesConfig.model_validate(with_bolagsverket(**{field: bad}))


def test_api_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(SCB_API_KEY, FAKE_KEY)
    assert api_key(SCB_API_KEY).get_secret_value() == FAKE_KEY


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_api_key_gives_a_clear_error(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(SCB_API_KEY, raising=False)
    else:
        monkeypatch.setenv(SCB_API_KEY, value)
    with pytest.raises(MissingApiKeyError, match=f"{SCB_API_KEY} saknas.*--env-file .env"):
        api_key(SCB_API_KEY)


def test_unknown_key_name_is_rejected():
    """Ett stavfel i koden ska inte bli ett "saknas"-fel som skickar dig till .env."""
    with pytest.raises(ValueError, match="okänd nyckel"):
        api_key("SCB_APIKEY")


def test_api_key_is_masked_when_printed_or_logged(monkeypatch, caplog):
    """D24: str(), repr(), f-strängar och loggar visar aldrig värdet."""
    monkeypatch.setenv(SCB_API_KEY, FAKE_KEY)
    key = api_key(SCB_API_KEY)
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("reacher").warning("nyckel: %s %r", key, key)
    for shown in (str(key), repr(key), f"{key}", caplog.text):
        assert FAKE_KEY not in shown


def test_env_example_lists_every_key_name_and_no_values():
    """.env.example är incheckad: bara namn (D24), och inget namn får saknas."""
    names = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, sep, value = line.partition("=")
        assert sep and value == "", f".env.example får bara ha namn, inga värden: {name}="
        names.append(name)
    assert sorted(names) == sorted(API_KEYS)


def test_check_sources_reports_the_key_without_showing_it(monkeypatch):
    monkeypatch.setenv(SCB_API_KEY, FAKE_KEY)
    result = runner.invoke(app, ["check-sources", "--config", str(COMMITTED_CONFIG)])
    assert result.exit_code == 0
    assert f"{SCB_API_KEY}: satt" in result.output
    assert FAKE_KEY not in result.output


def test_check_sources_fails_when_a_key_is_missing(monkeypatch):
    monkeypatch.delenv(SCB_API_KEY, raising=False)
    result = runner.invoke(app, ["check-sources", "--config", str(COMMITTED_CONFIG)])
    assert result.exit_code == 1
    assert f"{SCB_API_KEY} saknas" in result.output
