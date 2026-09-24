"""Enda källan för Excel-formatet. Ändra aldrig utan att säga till hela teamet:
både export (C) och import (D) läser härifrån.
"""
from enum import StrEnum


class Outcome(StrEnum):
    EJ_NADD     = "Ej nådd"
    INTRESSERAD = "Intresserad"
    REGISTRERAD = "Registrerad"
    NEJ         = "Nej"
    SPARRA      = "Spärra"


    SHEET = "Ringlista"
    META_SHEET = "_meta"

    COLUMNS: tuple[str, ...] = (
        "row_id",              # dold; call_list_row.id. Matchning sker ALLTID på den här.
        "Rang",
        "Poäng",
        "Salong",
        "Orgnr",
        "Område",
        "Telefon",
        "Källa",
        "Varför vi ringer",
        "Utfall",              # rullista
        "Kommentar",           # fritext
    )

    COL: dict[str, int] = {name: i for i, name in enumerate(COLUMNS, start=1)}

    EDITABLE = ("Utfall", "Kommentar")   # allt annat är låst
    HIDDEN = ("row_id",)

    META_KEYS = ("call_list_id", "week", "seller", "scoring_version", "generated_at")

    HEADER_ROW = 1
    FIRST_DATA_ROW = 2
