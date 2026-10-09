"""Enda källan för Excel-formatet. Ändra aldrig utan att säga till hela teamet:
både export (C) och import (D) läser härifrån.
"""

from enum import StrEnum


class Outcome(StrEnum):
    EJ_NADD = "Ej nådd"
    INTRESSERAD = "Intresserad"
    REGISTRERAD = "Registrerad"
    NEJ = "Nej"
    SPARRA = "Spärra"


SHEET = "Ringlista"
META_SHEET = "_meta"

COLUMNS: tuple[str, ...] = (
    "row_id",  # dold; call_list_row.id. Matchning sker ALLTID på den här.
    "Rang",
    "Poäng",
    "Salong",
    "Adress",
    "Ort",
    "Telefon",
    "Källa",
    "Omsättning",
    "Resultat",
    "Varför vi ringer",
    "Utfall",  # rullista
    "Kommentar",  # fritext
)

# Widths use the same order as COLUMNS. Excel width is measured approximately
# in the width of one character.
COLUMN_WIDTHS: dict[str, float] = {
    "row_id": 10,
    "Rang": 8,
    "Poäng": 10,
    "Salong": 28,
    "Adress": 28,
    "Ort": 18,
    "Telefon": 18,
    "Källa": 40,
    "Omsättning": 24,
    "Resultat": 24,
    "Varför vi ringer": 42,
    "Utfall": 16,
    "Kommentar": 40,
}

COL: dict[str, int] = {name: i for i, name in enumerate(COLUMNS, start=1)}

EDITABLE = ("Utfall", "Kommentar")  # allt annat är låst
HIDDEN = ("row_id",)

META_KEYS = ("call_list_id", "week", "seller", "scoring_version", "generated_at")

HEADER_ROW = 1
FIRST_DATA_ROW = 2
