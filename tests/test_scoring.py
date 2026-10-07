"""Scoring ur salongens fakta (T2-01, T2-08). Vikterna i testerna är egna, inte de
incheckade, så att tuning av scoring.yaml aldrig får testerna att gå sönder."""

from contextlib import closing
from datetime import date

import pytest
import yaml

from reacher.config import ScoringConfig
from reacher.db import connect, migrate
from reacher.scoring import (
    SalonFacts,
    months_before,
    score_salon,
    whole_months_between,
)

TODAY = date(2026, 9, 28)
EQUAL_WEIGHTS = {"registered_recently": 1, "small_employer": 1}


def make_config(weights=None, months=24, classes=("2",), disabled=()) -> ScoringConfig:
    weights = EQUAL_WEIGHTS if weights is None else weights
    return ScoringConfig.model_validate(
        {
            "version": "test",
            "half_life_days": 90,
            "signals": {
                "registered_recently": {
                    "enabled": "registered_recently" not in disabled,
                    "weight": weights["registered_recently"],
                    "months": months,
                },
                "small_employer": {
                    "enabled": "small_employer" not in disabled,
                    "weight": weights["small_employer"],
                    "classes": list(classes),
                },
            },
        }
    )


CFG = make_config()


def facts(registered_at=None, employee_class=None, salon_id=1) -> SalonFacts:
    return SalonFacts(salon_id=salon_id, registered_at=registered_at, employee_class=employee_class)


def rank(salons, cfg) -> list[int]:
    """Samma ordning som listan: högst poäng först, salon_id bryter lika."""
    ordered = sorted(salons, key=lambda s: (-score_salon(s, cfg, TODAY).total, s.salon_id))
    return [s.salon_id for s in ordered]


# --- registered_recently ---------------------------------------------------


@pytest.mark.parametrize(
    ("registered_at", "expected"),
    [
        (date(2024, 10, 28), 1),  # 23 månader
        (date(2024, 9, 28), 1),  # exakt 24 månader: "högst 24" räknas med
        (date(2024, 9, 27), 0),  # en dag över
        (date(2024, 8, 28), 0),  # 25 månader
        (TODAY, 1),  # registrerad i dag
    ],
)
def test_registered_recently_threshold(registered_at, expected):
    assert score_salon(facts(registered_at=registered_at), CFG, TODAY).total == expected


def test_registration_in_the_future_gives_nothing():
    """Ett framtida datum är ett datafel, inte en nystartad salong."""
    assert score_salon(facts(registered_at=date(2027, 1, 1)), CFG, TODAY).total == 0


@pytest.mark.parametrize(
    ("registered_at", "text"),
    [
        (date(2026, 9, 10), "Registrerad för mindre än en månad sedan"),
        (date(2026, 8, 28), "Registrerad för 1 månad sedan"),
        (date(2026, 1, 28), "Registrerad för 8 månader sedan"),
        (date(2026, 1, 29), "Registrerad för 7 månader sedan"),  # 8 månader fylls i morgon
    ],
)
def test_registered_reason_in_plain_swedish(registered_at, text):
    assert score_salon(facts(registered_at=registered_at), CFG, TODAY).reasons == (text,)


def test_threshold_counts_calendar_months_at_month_end():
    """31 mars minus en månad är 28 feb, inte 3 mars (vilket 30 dagar hade gett)."""
    cfg = make_config(months=1)
    today = date(2026, 3, 31)
    assert score_salon(facts(registered_at=date(2026, 2, 28)), cfg, today).total == 1
    assert score_salon(facts(registered_at=date(2026, 2, 27)), cfg, today).total == 0


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 9, 28), 24, date(2024, 9, 28)),
        (date(2026, 3, 31), 1, date(2026, 2, 28)),
        (date(2028, 2, 29), 24, date(2026, 2, 28)),  # skottdag
        (date(2026, 1, 15), 1, date(2025, 12, 15)),  # över årsskiftet
    ],
)
def test_months_before(day, months, expected):
    assert months_before(day, months) == expected


def test_whole_months_between_only_counts_full_months():
    assert whole_months_between(date(2026, 1, 31), date(2026, 2, 28)) == 0
    assert whole_months_between(date(2025, 12, 15), date(2026, 1, 15)) == 1


# --- small_employer ---------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("2", 1),  # 1-4 anställda
        ("1", 0),  # 0 anställda
        ("0", 0),  # uppgift saknas, inte noll anställda
        ("3", 0),
        ("1-4", 0),  # inte SCB:s format: koden är "2"
        (None, 0),
    ],
)
def test_small_employer_matches_scb_size_class_codes(code, expected):
    assert score_salon(facts(employee_class=code), CFG, TODAY).total == expected


def test_small_employer_reason_in_plain_swedish():
    assert score_salon(facts(employee_class="2"), CFG, TODAY).reasons == ("1-4 anställda",)


# --- summan och vikterna ----------------------------------------------------


def test_missing_facts_give_zero_not_a_crash():
    score = score_salon(facts(), CFG, TODAY)
    assert score.total == 0
    assert score.reasons == ()
    assert score.signals == ()


def test_both_signals_add_up():
    score = score_salon(facts(registered_at=date(2026, 1, 28), employee_class="2"), CFG, TODAY)
    assert score.total == 2
    assert score.reasons == ("Registrerad för 8 månader sedan", "1-4 anställda")
    assert score.signals == ("registered_recently", "small_employer")


def test_weights_come_from_config():
    cfg = make_config(weights={"registered_recently": 1, "small_employer": 2.5})
    assert score_salon(facts(employee_class="2"), cfg, TODAY).total == 2.5


# --- på/av och skuggläge (T2-08) --------------------------------------------


def test_shadow_signal_is_derived_but_gives_no_points_and_no_reason():
    """weight: 0 (D26): salongen har signalen, men säljaren ser den inte och den
    påverkar inte ordningen. Den måste ändå finnas i signals, annars kan T2-07
    aldrig mäta om den hjälper."""
    cfg = make_config(weights={"registered_recently": 1, "small_employer": 0})
    score = score_salon(facts(registered_at=date(2026, 1, 28), employee_class="2"), cfg, TODAY)
    assert score.total == 1
    assert score.reasons == ("Registrerad för 8 månader sedan",)
    assert score.signals == ("registered_recently", "small_employer")


def test_disabled_signal_is_not_derived_at_all():
    """enabled: false: varken poäng, skäl eller spår i signals, oavsett vikt."""
    cfg = make_config(disabled=("small_employer",))
    score = score_salon(facts(registered_at=date(2026, 1, 28), employee_class="2"), cfg, TODAY)
    assert score.total == 1
    assert score.reasons == ("Registrerad för 8 månader sedan",)
    assert score.signals == ("registered_recently",)


def test_all_signals_disabled_gives_zero():
    cfg = make_config(disabled=("registered_recently", "small_employer"))
    score = score_salon(facts(registered_at=date(2026, 1, 28), employee_class="2"), cfg, TODAY)
    assert (score.total, score.reasons, score.signals) == (0, (), ())


def test_changing_a_weight_in_scoring_yaml_changes_the_ranking(tmp_path):
    new_salon = facts(salon_id=1, registered_at=date(2026, 1, 28), employee_class="0")
    small_salon = facts(salon_id=2, registered_at=date(2015, 5, 1), employee_class="2")
    salons = [new_salon, small_salon]

    def ranking_with(weights):
        path = tmp_path / "scoring.yaml"
        payload = make_config(weights=weights).model_dump()
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return rank(salons, ScoringConfig.load(path))

    assert ranking_with({"registered_recently": 2, "small_employer": 1}) == [1, 2]
    assert ranking_with({"registered_recently": 1, "small_employer": 2}) == [2, 1]


# --- från databasen ---------------------------------------------------------


def test_facts_from_a_database_row(tmp_path):
    with closing(connect(tmp_path / "t.db")) as conn:
        migrate(conn)
        conn.executemany(
            "INSERT INTO salon (orgnr, name, registered_at, employee_class, "
            "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, '2026-09-28', '2026-09-28')",
            [
                ("5561234567", "Klipp & Co", "2026-01-28", "2"),
                ("5569876543", "Okänd Salong", None, None),
            ],
        )
        rows = conn.execute("SELECT * FROM salon ORDER BY id").fetchall()

    known, unknown = (SalonFacts.from_row(r) for r in rows)
    assert known.registered_at == date(2026, 1, 28)
    assert known.employee_class == "2"
    assert score_salon(known, CFG, TODAY).total == 2
    assert unknown.registered_at is None and unknown.employee_class is None
    assert score_salon(unknown, CFG, TODAY).total == 0
