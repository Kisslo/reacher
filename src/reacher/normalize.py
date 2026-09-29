"""Normalisering vid ingest (T1-04).

Källorna levererar data exakt som registret gör (base.py). All städning sker
här, så att CSV-källan och SCB-källan får samma regler.
"""

import phonenumbers

# SCB:s PeOrgNr är 12 siffror. 16 = juridisk person, 19/20 = seklet i ett
# personnummer (enskild firma). Alla tre tas bort så att nyckeln blir 10 siffror.
CENTURY_PREFIXES = ("16", "19", "20")


def luhn_ok(ten: str) -> bool:
    """Kontrollsiffran (sista siffran) i ett 10-siffrigt orgnr/personnummer."""
    total = 0
    for i, ch in enumerate(ten[:9]):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return (10 - total % 10) % 10 == int(ten[9])


def normalize_orgnr(raw: str | None) -> str | None:
    """10 siffror utan bindestreck och prefix, eller None om det inte är ett giltigt orgnr.

    Bara bindestreck och mellanslag tas bort. Andra tecken (bokstäver, punkter)
    ger None i stället för att tyst strykas: då är något fel i källan.
    """
    if not raw:
        return None
    digits = raw.replace("-", "").replace(" ", "")
    if not (digits.isascii() and digits.isdigit()):
        return None
    if len(digits) == 12 and digits[:2] in CENTURY_PREFIXES:
        digits = digits[2:]
    if len(digits) != 10 or not luhn_ok(digits):
        return None
    return digits


def normalize_phone(raw: str | None) -> str | None:
    """E.164 (t.ex. +46701740605), eller None om numret saknas eller är ogiltigt."""
    if not raw or not raw.strip():
        return None
    try:
        number = phonenumbers.parse(raw, "SE")
    except phonenumbers.NumberParseException:
        return None  # t.ex. "ring receptionen"
    if not phonenumbers.is_valid_number(number):
        return None  # t.ex. "070-12"
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
