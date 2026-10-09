"""Läser omsättning och resultat ur en årsredovisning i iXBRL (T1-13).

En ren funktion: xhtml in, (period_end, key, value) ut. Inget nätverk, ingen
databas och inget orgnr. Bolagsverket-adaptern (T1-14) hämtar dokumentet och
gör om varje ReportFact till en RawFinancial med orgnr och dokument-id.

Regler:
- Bara ix:nonFraction-taggarna i bolagsverket.tag_map läses (D29). En tagg som
  saknas ger inget värde, aldrig 0.
- Värdet = texten tolkad enligt format, gånger 10^scale, negativt om sign="-".
  Ett minustecken i texten bredvid taggen är bara layout och räknas inte.
- decimals anger hur exakt siffran är ("0", "-3", "INF"). Det ändrar aldrig värdet.
- contextRef pekar på en xbrli:context. period_end = endDate för ett räkenskapsår,
  instant för en balansdag. Kontexter med dimensioner (segment/scenario) gäller
  en del av företaget och hoppas över.
- Bara enheten iso4217:SEK och hela kronor (D25). Allt annat hoppas över med en
  varning, så att ingest aldrig får ett belopp i fel enhet.
- Samma värde står ofta två gånger (flerårsöversikt och resultaträkning). Lika
  värden blir ett. Olika värden ger inget värde alls: hellre en lucka än en gissning.
- Loggar dokument-id, aldrig orgnr. Rapportens xbrli:identifier är orgnr, och för
  en enskild firma ett personnummer.

Parsern är stdlibs ElementTree: iXBRL är XHTML och alltså giltig XML, så lxml
behövs inte. ElementTree hämtar aldrig externa entiteter, och expat (>= 2.4.1)
stoppar "billion laughs".
"""

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

log = logging.getLogger(__name__)

IX = "{http://www.xbrl.org/2013/inlineXBRL}"
XBRLI = "{http://www.xbrl.org/2003/instance}"
SEK = "iso4217:SEK"

# format (utan prefix) -> (tusentalsavgränsare, decimaltecken). Namnen kommer från
# XBRL:s Transformation Registry, där version 1-4 kallar samma sak olika saker.
# Saknas format är texten ett vanligt decimaltal. Okänt format -> inget värde.
NUMBER_FORMATS: dict[str | None, tuple[str, str]] = {
    None: ("", "."),
    "numspacecomma": (" ", ","),  # 1 234 567,89 (Bolagsverkets vanliga)
    "numdotcomma": (".", ","),  # 1.234.567,89
    "numcomma": ("", ","),  # 1234567,89
    "numspacedot": (" ", "."),  # 1 234 567.89
    "numcommadot": (",", "."),  # 1,234,567.89
    "numdotdecimal": (" ,", "."),
    "num-dot-decimal": (" ,", "."),
    "numcommadecimal": (" .", ","),
    "num-comma-decimal": (" .", ","),
}
# Formaten där ett streck eller en tom cell betyder ett rapporterat 0.
ZERO_FORMATS = {"zerodash", "numdash", "fixed-zero"}

_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")


@dataclass(frozen=True, slots=True)
class ReportFact:
    """Ett värde ur en årsredovisning. Adaptern lägger till orgnr och dokument-id."""

    period_end: date  # räkenskapsårets slutdatum (eller balansdagen)
    key: str  # nyckeln ur bolagsverket.tag_map, t.ex. "revenue"
    value: int  # hela kronor, tecknet behålls


class ReportParseError(ValueError):
    """Dokumentet går inte att läsa som iXBRL. Meddelandet innehåller bara dokument-id."""


class _Skip(Exception):
    """Ett värde som inte kan läsas säkert. Ger en varning och inget värde."""


def parse_annual_report(
    xhtml: bytes, tag_map: Mapping[str, str], document_id: str
) -> list[ReportFact]:
    """Alla värden för taggarna i tag_map, sorterade på (period_end, key).

    En årsredovisning har oftast med jämförelseår, och flerårsöversikten ofta
    fyra år. Alla år returneras. Vilka som sparas avgör adaptern och vyn
    latest_financial_fact, inte parsern.
    """
    try:
        root = ET.fromstring(xhtml)
    except ET.ParseError as e:
        # e säger rad och kolumn, aldrig innehållet.
        raise ReportParseError(f"Dokument {document_id}: inte giltig xhtml ({e})") from None
    if root.find(f".//{IX}header") is None:
        raise ReportParseError(f"Dokument {document_id}: inget ix:header, inte iXBRL 1.1")

    periods = _periods(root)
    sek_units = _sek_units(root)

    found: dict[tuple[date, str], set[int]] = {}
    for el in root.iter(f"{IX}nonFraction"):
        tag = el.get("name", "")
        key = tag_map.get(tag)
        if key is None:
            continue
        try:
            period_end, value = _read(el, periods, sek_units)
        except _Skip as reason:
            log.warning(
                "Dokument %s: %s (%s) hoppas över: %s",
                document_id,
                tag,
                el.get("contextRef"),
                reason,
            )
            continue
        found.setdefault((period_end, key), set()).add(value)

    facts = []
    for (period_end, key), values in sorted(found.items()):
        if len(values) > 1:
            log.warning(
                "Dokument %s: %s för %s har olika värden, inget sparas",
                document_id,
                key,
                period_end,
            )
            continue
        facts.append(ReportFact(period_end, key, values.pop()))

    for key in sorted(set(tag_map.values()) - {f.key for f in facts}):
        # Normalt för t.ex. ett holdingbolag utan omsättning. Ingen rad, aldrig 0.
        log.info("Dokument %s: %s saknas", document_id, key)
    return facts


def _periods(root: ET.Element) -> dict[str, date]:
    """contextRef -> slutdatum. Kontexter med dimensioner tas inte med."""
    periods = {}
    for ctx in root.iter(f"{XBRLI}context"):
        if ctx.find(f".//{XBRLI}segment") is not None:
            continue
        if ctx.find(f".//{XBRLI}scenario") is not None:
            continue
        end = ctx.find(f"{XBRLI}period/{XBRLI}endDate")
        if end is None:
            end = ctx.find(f"{XBRLI}period/{XBRLI}instant")
        if end is None or not end.text:
            continue
        # [:10]: ett datum kan skrivas som 2026-04-30T00:00:00.
        periods[ctx.get("id", "")] = date.fromisoformat(end.text.strip()[:10])
    return periods


def _sek_units(root: ET.Element) -> set[str]:
    """unitRef för enheter som är exakt kronor (inte procent, antal eller kr/aktie)."""
    return {
        unit.get("id", "")
        for unit in root.iter(f"{XBRLI}unit")
        if [(m.text or "").strip() for m in unit.iter(f"{XBRLI}measure")] == [SEK]
    }


def _read(el: ET.Element, periods: dict[str, date], sek_units: set[str]) -> tuple[date, int]:
    period_end = periods.get(el.get("contextRef", ""))
    if period_end is None:
        raise _Skip("okänd contextRef eller kontext med dimensioner")
    if el.get("unitRef") not in sek_units:
        raise _Skip("enheten är inte SEK")

    number = _number("".join(el.itertext()), el.get("format"))
    if number is None:
        raise _Skip(f"texten går inte att läsa med format {el.get('format')}")
    try:
        scale = int(el.get("scale", "0"))
    except ValueError:
        raise _Skip("ogiltig scale") from None
    # scale="3" betyder att rapporten står i tusentals kronor (tkr).
    value = number.scaleb(scale)
    if value != value.to_integral_value():
        raise _Skip("inte hela kronor")
    return period_end, -int(value) if el.get("sign") == "-" else int(value)


def _number(text: str, fmt: str | None) -> Decimal | None:
    """Texten som ett tal >= 0, eller None om den inte följer formatet."""
    name = fmt.split(":")[-1] if fmt else None
    if name in ZERO_FORMATS:
        return Decimal(0)
    if name not in NUMBER_FORMATS:
        return None
    thousands, decimal_sign = NUMBER_FORMATS[name]
    # \s tar även hårt mellanslag (&#160;) och smalt mellanslag, som rapporter
    # ofta har mellan tusentalen. Alla blir ett vanligt mellanslag.
    text = re.sub(r"\s+", " ", text).strip()
    for sep in thousands:
        text = text.replace(sep, "")
    text = text.replace(decimal_sign, ".")
    # Allt som är kvar ska vara ett decimaltal. Ett minustecken eller en bokstav
    # betyder att något är fel, och då gissar vi inte.
    return Decimal(text) if _DECIMAL.fullmatch(text) else None
