import pytest

from reacher.normalize import normalize_orgnr, normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5590001011", "5590001011"),  # redan normaliserat
        ("559000-1011", "5590001011"),  # bindestreck
        ("165590001011", "5590001011"),  # PeOrgNr, juridisk person
        ("19850312-2148", "8503122148"),  # enskild firma, 1900-tal
        ("200107053316", "0107053316"),  # enskild firma, 2000-tal
    ],
)
def test_valid_orgnr_becomes_ten_digits(raw, expected):
    assert normalize_orgnr(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "5560004046",  # fel kontrollsiffra
        "55612345",  # för kort
        "175590001011",  # 12 siffror men okänt prefix
        "55900O1011",  # bokstaven O, inte en nolla
    ],
)
def test_invalid_orgnr_is_rejected(raw):
    assert normalize_orgnr(raw) is None


@pytest.mark.parametrize(
    "raw",
    ["0701740605", "070-174 06 05", "+46701740605", "+46 70 174 06 05", "0046701740605"],
)
def test_mobile_formats_become_e164(raw):
    assert normalize_phone(raw) == "+46701740605"


def test_landline_becomes_e164():
    assert normalize_phone("08-465 004 10") == "+46846500410"


@pytest.mark.parametrize("raw", [None, "", "   ", "070-12", "ring receptionen"])
def test_missing_or_invalid_phone_is_none(raw):
    assert normalize_phone(raw) is None
