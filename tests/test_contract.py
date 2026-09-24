def test_row_id_is_first_and_hidden():
    from reacher.excel.contract import COL, HIDDEN, COLUMNS
    assert COLUMNS[0] == "row_id" and COL["row_id"] == 1
    assert "row_id" in HIDDEN


def test_outcome_values_are_exactly_the_five_in_the_spec():
    from reacher.excel.contract import Outcome
    assert [o.value for o in Outcome] == [
        "Ej nådd", "Intresserad", "Registrerad", "Nej", "Spärra"]
