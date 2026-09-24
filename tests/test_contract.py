"""Låser Excel-kontraktet. Bryts det ska CI faila, inte säljaren upptäcka det."""

from reacher.excel.contract import COL, COLUMNS, EDITABLE, HIDDEN, META_KEYS, Outcome


def test_row_id_is_first_and_hidden():
    assert COLUMNS[0] == "row_id"
    assert COL["row_id"] == 1
    assert "row_id" in HIDDEN


def test_outcome_values_are_exactly_the_five_in_the_spec():
    assert [o.value for o in Outcome] == [
        "Ej nådd",
        "Intresserad",
        "Registrerad",
        "Nej",
        "Spärra",
    ]


def test_only_utfall_and_kommentar_are_editable():
    assert EDITABLE == ("Utfall", "Kommentar")
    for col in EDITABLE:
        assert col in COLUMNS


def test_meta_carries_enough_to_reject_the_wrong_file():
    """Utan call_list_id och week kan importen inte vägra fjolårets fil."""
    assert "call_list_id" in META_KEYS
    assert "week" in META_KEYS
