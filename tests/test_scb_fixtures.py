"""Låser de inspelade SCB-svaren (T1-09). T1-06 testar adaptern mot dem, så de ska ha
exakt samma form som API:t och får aldrig innehålla riktiga uppgifter."""

import json
import re
from pathlib import Path

import pytest

from reacher.normalize import normalize_orgnr, normalize_phone

SCB = Path(__file__).parent / "fixtures" / "scb"
LIST_PAGE = "ae_naringsgren_96210.json"

# JSON-typ per kodfält, som det riktiga svaret levererar dem (docs/scb-fields.md).
# Adaptern gör str() på allt, men fixturen ska ha samma blandning som API:t.
# Annars testar T1-06 mot en snällare värld än den riktiga.
CODE_TYPES = {
    LIST_PAGE: {"aeStat": str, "anstKl": str, "reklamSparrTyp": int, "telefonSparrTyp": int},
    "ae_full.json": {"aeStat": int, "anstKl": int, "reklamSparrTyp": int, "telefonSparrTyp": int},
    "je_full.json": {
        "ftgStat": str,
        "jurform": str,
        "fSkattStat": str,
        "momsStat": str,
        "arbGivStat": str,
        "reklamSparrTyp": int,
        "telefonSparrTyp": int,
    },
}

# PTS:s nummerserier för fiktion, samma som i salons.csv (fixtures/README.md).
FICTION_PHONE = re.compile(r"^\+46(70174060[5-9]|7017406[1-9]\d|8465004\d\d)$")


def load(name: str):
    return json.loads((SCB / name).read_text(encoding="utf-8"))


def records(name: str) -> list[dict]:
    data = load(name)
    return data["arbetsstallen"] if "arbetsstallen" in data else [data]


def values(obj, key: str):
    """Alla värden för `key`, på vilken nivå som helst."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from values(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from values(item, key)


def test_every_recorded_response_is_checked():
    assert {p.name for p in SCB.glob("*.json")} == set(CODE_TYPES)


def test_list_page_uses_cursor_pagination():
    page = load(LIST_PAGE)
    assert set(page) == {"pagination", "arbetsstallen"}
    assert set(page["pagination"]) == {"nextCursorId", "limit", "hasMore"}


@pytest.mark.parametrize("name", CODE_TYPES)
def test_codes_keep_the_json_types_of_the_api(name):
    for record in records(name):
        for field, expected in CODE_TYPES[name].items():
            assert type(record[field]) is expected, f"{name}: {field}"


@pytest.mark.parametrize("name", CODE_TYPES)
def test_orgnr_are_valid_but_can_never_be_a_real_person(name):
    """Månad 13 finns inte, så ett påhittat personnummer kan aldrig tillhöra någon.
    Juridiska personer har 20 eller mer på månadens plats."""
    data = load(name)
    for raw in [*values(data, "peOrgNr"), *values(data, "orgNr")]:
        ten = normalize_orgnr(raw)
        assert ten is not None, f"{name}: ogiltigt orgnr"
        assert not 1 <= int(ten[2:4]) <= 12, f"{name}: ser ut som ett riktigt personnummer"


@pytest.mark.parametrize("name", CODE_TYPES)
def test_phones_are_in_the_fiction_series(name):
    """Tom sträng betyder att numret saknas, som i riktiga svaret."""
    for raw in values(load(name), "tel"):
        if raw.strip():
            assert FICTION_PHONE.match(normalize_phone(raw) or ""), f"{name}: {raw}"


def test_list_page_has_a_workplace_outside_the_municipality():
    """API:t kan inte filtrera på SNI och kommun samtidigt. T1-06 filtrerar kommunen
    själv, och det ska gå att testa."""
    assert {r["belagenhetsadress"]["kommun"] for r in records(LIST_PAGE)} > {"0180"}


def test_sole_trader_lives_somewhere_else_than_the_salon():
    """D19: postAdress och kommunSate är ägarens hem. T1-06 ska visa att de aldrig används."""
    ae, je = load("ae_full.json"), load("je_full.json")
    assert je["jurform"] == "10"
    assert je["kommunSate"] != ae["belagenhetsadress"]["kommun"]
    assert ae["postAdress"]["gatuAdress"] != ae["belagenhetsadress"]["bGatuAdress"]


def test_blank_means_missing_not_null():
    """API:t skickar "" eller " " i stället för null. T1-06 ska göra om dem till None."""
    ae = load("ae_full.json")
    assert ae["ben"] == " " and ae["tel"] == ""
    assert "slutDat" not in ae  # AE utelämnar fältet när det saknas


def test_dates_include_swedish_midnight():
    """2025-02-02T23:00Z är 2025-02-03 i Sverige. Datumdelen av strängen är en dag för tidig."""
    assert load("ae_full.json")["startDat"] == "2025-02-02T23:00:00.000Z"
