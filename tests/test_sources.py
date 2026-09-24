"""Låser källkontraktet. Varje ny adapter läggs till i SOURCES."""

import pytest

from reacher.sources.base import (
    CSV_COLUMNS,
    SIGNAL_CSV_COLUMNS,
    RawSalon,
    RawSignal,
    SalonSource,
)
from reacher.sources.scb import ScbApiSource

SOURCES = [ScbApiSource()]


@pytest.mark.parametrize("source", SOURCES, ids=lambda s: s.name)
def test_every_source_satisfies_the_protocol(source):
    assert isinstance(source, SalonSource)


def test_object_without_fetch_is_not_a_source():
    class NotASource:
        name = "x"

    assert not isinstance(NotASource(), SalonSource)


def test_raw_salon_needs_only_orgnr_and_name():
    salon = RawSalon(orgnr="5561234567", name="Klipp & Co")
    assert salon.cfar is None and salon.phone is None
    assert salon.signals == ()


def test_salon_carries_its_signals():
    signal = RawSignal(key="advertises_chair", evidence="Stol att hyra!")
    salon = RawSalon(orgnr="5561234567", name="Klipp & Co", signals=(signal,))
    assert salon.signals[0].key == "advertises_chair"
    assert salon.signals[0].value == 1.0


def test_csv_columns_follow_raw_salon():
    """A1 och A3 bygger på den här ordningen; ändras den ska det märkas."""
    assert CSV_COLUMNS[:2] == ("orgnr", "name")
    assert "signals" not in CSV_COLUMNS  # signaler ligger i signals.csv
    assert len(CSV_COLUMNS) == 13


def test_signal_csv_columns_link_to_salon_and_mirror_the_table():
    assert SIGNAL_CSV_COLUMNS == (
        "orgnr",
        "cfar",
        "key",
        "value",
        "evidence",
        "source_url",
        "observed_at",
    )


def test_scb_stub_fails_loudly():
    with pytest.raises(NotImplementedError):
        next(iter(ScbApiSource().fetch()))
