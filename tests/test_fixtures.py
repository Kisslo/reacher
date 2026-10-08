"""Låser T1-03: fixturerna ska täcka varje kantfall, annars testar T1-05 och
Team 2 mot en snällare värld än den riktiga. Tas ett kantfall bort ska det märkas."""

import csv
from pathlib import Path

import pytest

from reacher.normalize import normalize_orgnr as orgnr_key
from reacher.sources.base import CSV_COLUMNS, SIGNAL_CSV_COLUMNS

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> list[dict[str, str]]:
    with open(FIXTURES / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def header(name: str) -> tuple[str, ...]:
    with open(FIXTURES / name, encoding="utf-8", newline="") as f:
        return tuple(next(csv.reader(f)))


@pytest.fixture(scope="module")
def salons() -> list[dict[str, str]]:
    return read("salons.csv")


def column(rows, name) -> set[str]:
    return {r[name] for r in rows}


# --- Form ------------------------------------------------------------------


def test_headers_follow_the_source_contract():
    assert header("salons.csv") == CSV_COLUMNS
    assert header("signals.csv") == SIGNAL_CSV_COLUMNS
    assert header("ground_truth.csv") == ("orgnr", "cfar", "has_empty_chairs")


def test_about_sixty_salons(salons):
    assert 55 <= len(salons) <= 80


def test_no_null_markers(salons):
    """Tom cell betyder None. 'NULL', 'None' och '-' är fel (se base.py)."""
    for r in salons:
        for value in r.values():
            assert value not in ("NULL", "None", "null", "-", "nan")


def test_dates_are_iso(salons):
    for r in salons:
        d = r["registered_at"]
        assert d == "" or (len(d) == 10 and d[4] == d[7] == "-"), d


def test_ground_truth_never_leaks_into_salons():
    assert "has_empty_chairs" not in header("salons.csv")


# --- Täckning (acceptanskriterierna i #12) ----------------------------------


def test_orgnr_formats(salons):
    raws = [r["orgnr"] for r in salons]
    assert any("-" in o and len(o) == 11 for o in raws), "10 siffror med bindestreck"
    assert any(o.isdigit() and len(o) == 10 for o in raws), "10 siffror utan"
    assert any(o.startswith("16") and len(o) == 12 for o in raws), "PeOrgNr 16..."
    assert any(o.startswith("19") for o in raws), "PeOrgNr 19... (enskild firma)"
    assert any(o.startswith("20") and len(o) == 12 for o in raws), "PeOrgNr 20..."
    assert "" in raws, "saknat orgnr"
    assert any(o and orgnr_key(o) is None for o in raws), "ogiltigt orgnr"


def test_same_orgnr_with_several_workplaces(salons):
    cfars: dict[str, set[str]] = {}
    for r in salons:
        if key := orgnr_key(r["orgnr"]):
            cfars.setdefault(key, set()).add(r["cfar"])
    assert max(len(c) for c in cfars.values()) >= 3


def test_duplicate_salon_in_different_formats(salons):
    keys = [(orgnr_key(r["orgnr"]), r["cfar"]) for r in salons if orgnr_key(r["orgnr"])]
    dups = {k for k in keys if keys.count(k) > 1}
    assert any(cfar for _, cfar in dups), "dubblett med cfar"
    assert any(not cfar for _, cfar in dups), "dubblett utan cfar (IFNULL-fällan)"


def test_phone_formats(salons):
    phones = [r["phone"] for r in salons]
    assert "" in phones
    assert any(p.startswith("+46") for p in phones)
    assert any(p.startswith("070-") for p in phones)
    assert any(p.startswith("08") for p in phones)
    assert any(p and sum(c.isdigit() for c in p) < 8 for p in phones), "ogiltigt nummer"


def test_legal_forms(salons):
    assert {"10", "49", "31", "99", ""} <= column(salons, "legal_form")


SOLE_PROP_CASES = [
    # (ftax, vat, employer) som T1-05 måste avgöra rätt
    ("1", "0", "0"),
    ("0", "1", "0"),
    ("0", "3", "0"),
    ("0", "0", "1"),
    ("0", "0", "3"),
    ("0", "0", "2"),
    ("0", "0", "0"),
    ("9", "9", "9"),
    ("", "", ""),
    ("1", "", ""),
]


@pytest.mark.parametrize("combo", SOLE_PROP_CASES, ids=lambda c: "/".join(x or "-" for x in c))
def test_sole_proprietorship_combination_exists(salons, combo):
    found = {
        (r["ftax_status"], r["vat_status"], r["employer_status"])
        for r in salons
        if r["legal_form"] == "10"
    }
    assert combo in found


BLOCK_FIELDS = (
    "ad_block_type",
    "phone_block_type",
    "workplace_ad_block_type",
    "workplace_phone_block_type",
)


def no_block(r) -> bool:
    return all(r[f] == "1" for f in BLOCK_FIELDS)


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("ad_block_type", "2"),
        ("ad_block_type", ""),
        ("ad_block_type", "7"),  # okänd kod
        ("phone_block_type", "2"),
        ("phone_block_type", "3"),  # NIX-Telefon
        ("phone_block_type", ""),
        ("company_status", "0"),
        ("company_status", "9"),
        ("company_status", ""),
        ("workplace_status", "0"),
        ("workplace_status", "9"),
        ("workplace_status", ""),
    ],
)
def test_every_company_code(salons, field, code):
    assert code in column(salons, field)


def test_only_workplace_blocks(salons):
    """Företaget har ingen spärr men arbetsstället har, en gång per fält."""
    company_ok = [r for r in salons if r["ad_block_type"] == r["phone_block_type"] == "1"]
    assert any(r["workplace_ad_block_type"] == "2" for r in company_ok)
    assert any(r["workplace_phone_block_type"] == "3" for r in company_ok)
    assert any(
        r["workplace_ad_block_type"] == r["workplace_phone_block_type"] == "" for r in company_ok
    )


def test_blocked_salon_that_would_score_high(salons):
    assert any(
        not no_block(r) and r["employee_class"] == "2" and r["registered_at"] >= "2024-09-28"
        for r in salons
    )


def test_legal_person_with_unknown_status_and_no_ad_block(salons):
    assert any(
        r["legal_form"] == "49"
        and r["ftax_status"] == r["vat_status"] == r["employer_status"] == ""
        and no_block(r)
        for r in salons
    )


def test_closed_workplace_of_an_active_company(salons):
    """D33: företaget är verksamt men just den här salongen är nedlagd."""
    assert any(r["company_status"] == "1" and r["workplace_status"] == "9" for r in salons)


def test_estate_with_every_registration(salons):
    """D30: dödsboet ska uteslutas fast det har F-skatt, moms och arbetsgivare."""
    assert any(
        r["legal_form"] == "91"
        and r["ftax_status"] == r["vat_status"] == r["employer_status"] == "1"
        for r in salons
    )


def test_missing_legal_form_with_ftax(salons):
    """D30 får inte råka utesluta en salong utan juridisk form (D22 avgör den)."""
    assert any(r["legal_form"] == "" and r["ftax_status"] == "1" and no_block(r) for r in salons)


def test_registration_dates_on_both_sides_of_24_months(salons):
    dates = {r["registered_at"] for r in salons}
    assert any(d and d >= "2024-09-28" for d in dates)
    assert any(d and d < "2024-09-28" for d in dates)
    assert "" in dates


def test_employee_size_classes(salons):
    """SCB Storleksklass Anställda: 0 = uppgift saknas, 1 = 0 anst., 2 = 1-4."""
    assert {"0", "1", "2", ""} <= column(salons, "employee_class")
    assert column(salons, "employee_class") - {""} <= {str(i) for i in range(17)}


# --- Ground truth ------------------------------------------------------------


def test_ground_truth_covers_exactly_the_valid_salons(salons):
    valid = {(orgnr_key(r["orgnr"]), r["cfar"]) for r in salons if orgnr_key(r["orgnr"])}
    truth = {(r["orgnr"], r["cfar"]) for r in read("ground_truth.csv")}
    assert truth == valid


def test_ground_truth_is_boolean_and_not_trivial():
    values = [r["has_empty_chairs"] for r in read("ground_truth.csv")]
    assert set(values) == {"0", "1"}
