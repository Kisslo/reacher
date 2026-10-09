"""Scoring ur salongens fakta (T2-01, T2-08, T2-09). Vikterna i testerna är egna,
inte de incheckade, så att tuning av scoring.yaml aldrig får testerna att gå sönder.
Här har alla signaler poäng och är påslagna, så att varje signal syns i total."""

from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
import yaml

from reacher.config import ScoringConfig
from reacher.db import connect, migrate
from reacher.scoring import (
    NET_RESULT,
    REVENUE,
    FiscalYear,
    SalonFacts,
    fiscal_years_by_orgnr,
    months_before,
    score_salon,
    whole_months_between,
)
from reacher.sources.config import SourcesConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 28)
EQUAL_WEIGHTS = {
    "registered_recently": 1,
    "small_employer": 1,
    "loss_making": 1,
    "low_revenue": 1,
    "declining_revenue": 1,
}


def make_config(
    weights=None, months=24, classes=("2",), below_sek=500_000, years=3, disabled=()
) -> ScoringConfig:
    weights = {**EQUAL_WEIGHTS, **(weights or {})}

    def block(key, **settings):
        return {"enabled": key not in disabled, "weight": weights[key], **settings}

    return ScoringConfig.model_validate(
        {
            "version": "test",
            "half_life_days": 90,
            "signals": {
                "registered_recently": block("registered_recently", months=months),
                "small_employer": block("small_employer", classes=list(classes)),
                "loss_making": block("loss_making"),
                "low_revenue": block("low_revenue", below_sek=below_sek),
                "declining_revenue": block("declining_revenue", years=years),
            },
        }
    )


CFG = make_config()


def facts(registered_at=None, employee_class=None, salon_id=1, fiscal_years=()) -> SalonFacts:
    return SalonFacts(
        salon_id=salon_id,
        registered_at=registered_at,
        employee_class=employee_class,
        fiscal_years=tuple(fiscal_years),
    )


def year(end_year, revenue=None, net_result=None, month=12, day=31) -> FiscalYear:
    return FiscalYear(date(end_year, month, day), revenue=revenue, net_result=net_result)


def signals_of(salon, cfg=CFG) -> tuple[str, ...]:
    return score_salon(salon, cfg, TODAY).signals


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


# --- finansiella signaler (T2-09) -------------------------------------------


def test_company_without_annual_reports_gets_no_financial_signal():
    """Enskild firma eller AB utan rapport: varken förlust eller vinst, låg eller hög."""
    assert signals_of(facts()) == ()


@pytest.mark.parametrize(
    ("net_result", "expected"),
    [
        (-87_400, ("loss_making",)),
        (-1, ("loss_making",)),
        (0, ()),  # noll är ett värde, ingen förlust
        (312_450, ()),
        (None, ()),
    ],
)
def test_loss_making_is_a_negative_result_in_the_latest_year(net_result, expected):
    salon = facts(fiscal_years=[year(2024, revenue=4_000_000, net_result=net_result)])
    assert signals_of(salon) == expected


def test_loss_making_never_falls_back_to_an_older_year():
    """Saknar senaste rapporten net_result räknas inte förra årets förlust (D34)."""
    salon = facts(
        fiscal_years=[
            year(2024, revenue=1_567_800),
            year(2023, revenue=1_498_200, net_result=-67_300),
        ]
    )
    assert signals_of(salon) == ()


def test_an_older_loss_does_not_count_when_the_latest_year_is_a_profit():
    salon = facts(
        fiscal_years=[
            year(2025, revenue=965_400, net_result=34_800, month=4, day=30),
            year(2024, revenue=912_750, net_result=-12_600, month=4, day=30),
        ]
    )
    assert signals_of(salon) == ()


@pytest.mark.parametrize(
    ("revenue", "expected"),
    [
        (312_400, ("low_revenue",)),
        (499_999, ("low_revenue",)),
        (500_000, ()),  # strikt under gränsen
        (4_812_300, ()),
        (0, ("low_revenue",)),  # rapporterad nolla är ett värde
        (None, ()),
    ],
)
def test_low_revenue_is_below_the_threshold_in_the_latest_year(revenue, expected):
    salon = facts(fiscal_years=[year(2024, revenue=revenue, net_result=10_000)])
    assert signals_of(salon) == expected


def test_low_revenue_threshold_comes_from_config():
    salon = facts(fiscal_years=[year(2024, revenue=965_400, net_result=10_000)])
    assert signals_of(salon) == ()
    assert signals_of(salon, make_config(below_sek=1_000_000)) == ("low_revenue",)


def test_low_revenue_never_falls_back_to_an_older_year():
    salon = facts(fiscal_years=[year(2024, net_result=10_000), year(2023, revenue=100_000)])
    assert signals_of(salon) == ()


def test_financial_reasons_in_plain_swedish():
    salon = facts(fiscal_years=[year(2025, revenue=312_400, net_result=-45_300)])
    assert score_salon(salon, CFG, TODAY).reasons == (
        "Gick med förlust senaste räkenskapsåret",
        "Omsättning under 500 000 kr senaste räkenskapsåret",
    )


DECLINING = [
    year(2024, revenue=1_245_600, net_result=10_000),
    year(2023, revenue=1_389_200),
    year(2022, revenue=1_402_750),
]


def test_declining_revenue_three_years_in_a_row():
    assert signals_of(facts(fiscal_years=DECLINING)) == ("declining_revenue",)
    assert score_salon(facts(fiscal_years=DECLINING), CFG, TODAY).reasons == (
        "Minskande omsättning 3 räkenskapsår i rad",
    )


def test_declining_revenue_with_broken_fiscal_years():
    salon = facts(
        fiscal_years=[
            year(2025, revenue=800_000, net_result=10_000, month=4, day=30),
            year(2024, revenue=900_000, month=4, day=30),
            year(2023, revenue=1_000_000, month=4, day=30),
        ]
    )
    assert signals_of(salon) == ("declining_revenue",)


@pytest.mark.parametrize(
    "fiscal_years",
    [
        pytest.param(DECLINING[:2], id="only-two-years"),
        pytest.param([DECLINING[0], DECLINING[1], year(2022, revenue=1_389_200)], id="flat-year"),
        pytest.param([DECLINING[0], year(2023, revenue=1_000_000), DECLINING[2]], id="went-up"),
        pytest.param([DECLINING[0], year(2023), DECLINING[2]], id="revenue-missing-one-year"),
        pytest.param(
            [DECLINING[0], DECLINING[2], year(2021, revenue=1_500_000)], id="gap-in-the-years"
        ),
        pytest.param(
            [
                DECLINING[0],
                year(2023, revenue=1_389_200, month=4, day=30),
                year(2022, revenue=1_402_750, month=4, day=30),
            ],
            id="changed-fiscal-year",
        ),
    ],
)
def test_declining_revenue_needs_comparable_years(fiscal_years):
    assert signals_of(facts(fiscal_years=fiscal_years)) == ()


def test_declining_revenue_years_come_from_config():
    salon = facts(fiscal_years=DECLINING[:2])
    assert signals_of(salon, make_config(years=2)) == ("declining_revenue",)


def test_financial_shadow_signals_are_stored_but_change_nothing():
    """Så som scoring.yaml har dem (D26): samma poäng och skäl som utan dem."""
    shadow = make_config(weights={"loss_making": 0, "low_revenue": 0, "declining_revenue": 0})
    without = make_config(disabled=("loss_making", "low_revenue", "declining_revenue"))
    salon = facts(
        employee_class="2",
        fiscal_years=[
            year(2024, revenue=312_400, net_result=-45_300),
            year(2023, revenue=400_000),
            year(2022, revenue=450_000),
        ],
    )
    shadowed, plain = score_salon(salon, shadow, TODAY), score_salon(salon, without, TODAY)
    assert (shadowed.total, shadowed.reasons) == (plain.total, plain.reasons)
    assert plain.reasons == ("1-4 anställda",)
    assert shadowed.signals == ("small_employer", "loss_making", "low_revenue", "declining_revenue")
    assert plain.signals == ("small_employer",)


def test_fiscal_years_are_grouped_per_company_newest_first():
    rows = [
        {"orgnr": "5560002023", "period_end": "2023-12-31", "key": "revenue", "value": 2},
        {"orgnr": "5560002023", "period_end": "2024-12-31", "key": "net_result", "value": -1},
        {"orgnr": "5560002023", "period_end": "2024-12-31", "key": "revenue", "value": 1},
        {"orgnr": "5590009550", "period_end": "2025-12-31", "key": "revenue", "value": 3},
        # En ny rad i tag_map som ingen signal använder ännu.
        {"orgnr": "5590009550", "period_end": "2025-12-31", "key": "employees", "value": 4},
    ]
    assert fiscal_years_by_orgnr(rows) == {
        "5560002023": (year(2024, revenue=1, net_result=-1), year(2023, revenue=2)),
        "5590009550": (year(2025, revenue=3),),
    }


def test_financial_keys_match_team_1s_tag_map():
    """Byter Team 1 namn på en nyckel ska det synas här, inte som tysta nollor."""
    tag_map = SourcesConfig.load(REPO_ROOT / "sources.yaml").bolagsverket.tag_map
    assert {REVENUE, NET_RESULT} <= set(tag_map.values())


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
