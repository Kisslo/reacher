"""T1-13: iXBRL-parsern. Fiktiv årsredovisning i tests/fixtures/bolagsverket/.

Kantfallen byggs som små dokument här i filen, så att fixturen kan se ut som en
riktig rapport utan varningar.
"""

import logging
from datetime import date
from pathlib import Path

import pytest

from reacher.sources.config import SourcesConfig
from reacher.sources.ixbrl import ReportFact, ReportParseError, parse_annual_report

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = Path(__file__).parent / "fixtures" / "bolagsverket" / "arsredovisning_fiktiv.xhtml"
SAMPLE_ORGNR = "559999-0016"  # står i fixturens xbrli:identifier, får aldrig loggas
DOC = "FIKTIV-T113"

REVENUE = "se-gen-base:Nettoomsattning"
RESULT = "se-gen-base:AretsResultat"
TAG_MAP = {REVENUE: "revenue", RESULT: "net_result"}

FY = date(2026, 4, 30)  # räkenskapsårets slut i period0 nedan


def parse(xhtml: bytes, tag_map=None) -> list[ReportFact]:
    return parse_annual_report(xhtml, tag_map or TAG_MAP, DOC)


# --- Fixturen ----------------------------------------------------------------


def test_sample_gives_revenue_and_result_for_every_year():
    """Flerårsöversikten ger 4 års omsättning, resultaträkningen 2 års resultat.
    Omsättningen står två gånger för de två senaste åren, med samma värde."""
    tag_map = SourcesConfig.load(REPO_ROOT / "sources.yaml").bolagsverket.tag_map
    assert parse(SAMPLE.read_bytes(), tag_map) == [
        ReportFact(date(2023, 4, 30), "revenue", 3_912_040),
        ReportFact(date(2024, 4, 30), "revenue", 4_105_220),
        ReportFact(date(2025, 4, 30), "net_result", -48_210),
        ReportFact(date(2025, 4, 30), "revenue", 4_388_615),
        ReportFact(date(2026, 4, 30), "net_result", 126_455),
        ReportFact(date(2026, 4, 30), "revenue", 4_702_330),
    ]


def test_similar_tag_names_are_not_mixed_up():
    """AretsResultatEgetKapital är inte AretsResultat: taggen måste matcha exakt."""
    facts = parse(SAMPLE.read_bytes(), {"se-gen-base:AretsResultat": "net_result"})
    assert {f.value for f in facts} == {126_455, -48_210}
    assert len(facts) == 2


def test_new_field_is_one_tag_map_line():
    """D29: ett nytt fält är en rad i tag_map. Balansposter har en instant-kontext."""
    facts = parse(SAMPLE.read_bytes(), {"se-gen-base:Aktiekapital": "share_capital"})
    assert facts == [ReportFact(date(2026, 4, 30), "share_capital", 25_000)]


def test_logs_document_id_never_orgnr(caplog):
    caplog.set_level(logging.INFO)
    # Soliditet är procent (varning), net_result finns inte i tag_map (saknas-rad).
    parse(SAMPLE.read_bytes(), {REVENUE: "revenue", "se-gen-base:Soliditet": "solidity"})
    assert DOC in caplog.text
    assert "solidity" in caplog.text
    for orgnr in (SAMPLE_ORGNR, SAMPLE_ORGNR.replace("-", "")):
        assert orgnr not in caplog.text


# --- Små dokument för kantfallen -------------------------------------------------

CONTEXTS = """
<xbrli:context id="period0">
  <xbrli:entity><xbrli:identifier scheme="http://www.bolagsverket.se">559999-0016</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2025-05-01</xbrli:startDate><xbrli:endDate>2026-04-30</xbrli:endDate></xbrli:period>
</xbrli:context>
<xbrli:context id="dim0">
  <xbrli:entity>
    <xbrli:identifier scheme="http://www.bolagsverket.se">559999-0016</xbrli:identifier>
    <xbrli:segment>
      <xbrldi:explicitMember dimension="se-gen-base:Del">x</xbrldi:explicitMember>
    </xbrli:segment>
  </xbrli:entity>
  <xbrli:period><xbrli:startDate>2025-05-01</xbrli:startDate><xbrli:endDate>2026-04-30</xbrli:endDate></xbrli:period>
</xbrli:context>
<xbrli:unit id="SEK"><xbrli:measure>iso4217:SEK</xbrli:measure></xbrli:unit>
<xbrli:unit id="EUR"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>
"""


def report(*facts: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2010-04-20"
      xmlns:ixt4="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:se-gen-base="http://www.taxonomier.se/se/fr/gen-base/2021-10-31">
<head><title>Fiktiv</title></head>
<body>
<ix:header><ix:resources>{CONTEXTS}</ix:resources></ix:header>
{"".join(facts)}
</body>
</html>""".encode()


def fact(text: str, name: str = REVENUE, **attrs: str | None) -> str:
    """En ix:nonFraction. attr=None tar bort attributet."""
    defaults = {
        "contextRef": "period0",
        "unitRef": "SEK",
        "format": "ixt:numspacecomma",
        "scale": "0",
        "decimals": "0",
    }
    merged = {k: v for k, v in {**defaults, **attrs}.items() if v is not None}
    attributes = " ".join(f'{k}="{v}"' for k, v in merged.items())
    return f'<p><ix:nonFraction name="{name}" {attributes}>{text}</ix:nonFraction></p>'


def values(xhtml: bytes) -> list[int]:
    return [f.value for f in parse(xhtml)]


@pytest.mark.parametrize(
    ("text", "scale", "decimals", "expected"),
    [
        ("4 702 330", "0", "0", 4_702_330),
        ("4 702", "3", "-3", 4_702_000),  # rapport i tkr
        ("4,7", "6", "-5", 4_700_000),  # rapport i mkr
        ("4 702 330", "0", "INF", 4_702_330),  # decimals ändrar aldrig värdet
        ("0", "0", "0", 0),  # ett rapporterat 0 är ett värde
    ],
)
def test_scale_multiplies_and_decimals_is_ignored(text, scale, decimals, expected):
    assert values(report(fact(text, scale=scale, decimals=decimals))) == [expected]


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        (fact("48 210", sign="-"), -48_210),
        ("-" + fact("48 210", sign="-"), -48_210),  # strecket före taggen är layout
        ("-" + fact("48 210"), 48_210),  # utan sign är värdet positivt
    ],
)
def test_only_the_sign_attribute_makes_a_value_negative(markup, expected):
    assert values(report(markup)) == [expected]


@pytest.mark.parametrize(
    ("text", "fmt", "expected"),
    [
        ("4&#160;702&#160;330", "ixt:numspacecomma", 4_702_330),  # hårt mellanslag
        ("4.702.330", "ixt:numdotcomma", 4_702_330),
        ("4,702,330", "ixt:numcommadot", 4_702_330),
        ("4 702 330.00", "ixt4:num-dot-decimal", 4_702_330),
        ("4702330", None, 4_702_330),  # inget format = vanligt decimaltal
        ("-", "ixt:zerodash", 0),
        ("–", "ixt4:fixed-zero", 0),
    ],
)
def test_number_formats(text, fmt, expected):
    assert values(report(fact(text, format=fmt))) == [expected]


@pytest.mark.parametrize(
    ("markup", "reason"),
    [
        (fact("4 702,50"), "inte hela kronor"),
        (fact("4 702 330", format="ixt:datelonguk"), "format"),
        (fact("4 7O2 330"), "format"),  # bokstaven O
        (fact("", format="ixt:numspacecomma"), "format"),
        (fact("4 702 330", unitRef="EUR"), "SEK"),
        (fact("4 702 330", contextRef="period9"), "contextRef"),
        (fact("4 702 330", contextRef="dim0"), "dimensioner"),
        (fact("4 702 330", scale="tre"), "scale"),
    ],
)
def test_unreadable_value_gives_no_value_and_a_warning(markup, reason, caplog):
    assert values(report(markup)) == []
    assert DOC in caplog.text
    assert reason in caplog.text


def test_missing_tag_gives_no_value_never_zero(caplog):
    caplog.set_level(logging.INFO)
    facts = parse(report(fact("4 702 330")))
    assert facts == [ReportFact(FY, "revenue", 4_702_330)]
    assert f"Dokument {DOC}: net_result saknas" in caplog.text


def test_equal_duplicates_become_one():
    assert values(report(fact("4 702 330"), fact("4&#160;702&#160;330"))) == [4_702_330]


def test_conflicting_duplicates_give_no_value(caplog):
    """Hellre en lucka än att gissa vilken siffra som stämmer."""
    assert values(report(fact("4 702 330"), fact("4 702 331"))) == []
    assert "olika värden" in caplog.text


def test_tags_outside_the_tag_map_are_ignored():
    assert values(report(fact("158 120", name="se-gen-base:ResultatEfterFinansiellaPoster"))) == []


@pytest.mark.parametrize(
    "xhtml",
    [
        b"<html><body><p>trasig</body></html>",
        b"%PDF-1.7 inte xhtml",
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>Ingen iXBRL</body></html>',
    ],
)
def test_broken_document_raises_with_document_id(xhtml):
    with pytest.raises(ReportParseError, match=DOC):
        parse(xhtml)
