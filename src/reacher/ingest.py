"""Ingest (T1-04): normalisera RawSalon och upserta i salon + contact.

Regler:
- Ogiltigt eller saknat orgnr -> avvisas. Utan orgnr går salongen inte att spärra.
- Ogiltigt telefonnummer -> salongen sparas, numret gör det inte.
- Alla fält skrivs över, även med NULL. Säger SCB inte längre något om
  ad_block_type ska den gamla koden inte ligga kvar: då failar vyn öppet.
- E-post sparas aldrig (dataminimering, Format 1). RawSalon har inget sådant fält.
- Signaler skrivs inte: signal-tabellen används inte i MVP:n (D4).
- Varje ingest är en körning i ingest_run (T1-07, D38). Det källan inte levererar
  har försvunnit: callable_salon släpper bara igenom den senaste körningens salonger.
  En körning som kraschar eller inte ger någon giltig salong sparas inte alls.

ingest_financials (T1-12) upsertar RawFinancial i financial_fact:
- Samma orgnr-regel som för salonger. Ett företag behöver inte ha en salong i
  databasen: fakta är per företag, och salongen kan komma i en senare körning.
- Saknat värde eller ett värde som inte är hela kronor -> avvisas, aldrig 0.
- Samma värde igen -> raden rörs inte, så en omkörning ger samma databas.

financial_candidates och stored_fiscal_years (T1-14) säger vad Bolagsverket-adaptern
ska hämta: ringbara företag (D25) och de räkenskapsår som redan finns.
"""

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from reacher.db import now as utc_now
from reacher.normalize import normalize_orgnr, normalize_phone
from reacher.sources.base import RawFinancial, RawSalon

log = logging.getLogger(__name__)

# Kopieras rakt av från RawSalon till salon. orgnr och cfar är nyckeln;
# website och phone blir contact-rader.
SALON_FIELDS = (
    "name",
    "sni",
    "street",
    "postal_code",
    "city",
    "municipality",
    "employee_class",
    "registered_at",
    "legal_form",
    "ftax_status",
    "vat_status",
    "employer_status",
    "company_status",
    "workplace_status",
    "ad_block_type",
    "phone_block_type",
    "workplace_ad_block_type",
    "workplace_phone_block_type",
)

# Byggs av konstanta kolumnnamn, aldrig av data. Värdena går som parametrar.
# ON CONFLICT pekar på uttrycksindexet salon_identity, så att cfar = NULL
# matchar cfar = NULL. first_seen_at finns inte i DO UPDATE och behåller därför
# sitt första värde, även för en salong som försvann och kom tillbaka.
UPSERT_SALON = f"""
INSERT INTO salon (
    orgnr, cfar, {", ".join(SALON_FIELDS)}, first_seen_at, last_seen_at, last_seen_run
)
VALUES (:orgnr, :cfar, {", ".join(":" + f for f in SALON_FIELDS)}, :now, :now, :run_id)
ON CONFLICT (orgnr, IFNULL(cfar, '')) DO UPDATE SET
    {", ".join(f"{f} = excluded.{f}" for f in SALON_FIELDS)},
    last_seen_at = excluded.last_seen_at,
    last_seen_run = excluded.last_seen_run
RETURNING id
"""

# DO NOTHING: found_at behåller första gången vi såg numret, så en omkörning
# ändrar ingenting.
INSERT_CONTACT = """
INSERT INTO contact (salon_id, kind, value, found_at) VALUES (?, ?, ?, ?)
ON CONFLICT (salon_id, kind, value) DO NOTHING
"""


class EmptyRunError(RuntimeError):
    """Körningen gav inga giltiga salonger. Troligare ett fel hos källan än att
    alla salonger har stängt, så körningen sparas inte (D38)."""


@dataclass
class Summary:
    inserted: int = 0
    updated: int = 0
    rejected: int = 0
    invalid_phones: int = 0
    gone: int = 0  # fanns i förra körningen men inte i den här (T1-07)


def ingest(
    conn: sqlite3.Connection,
    salons: Iterable[RawSalon],
    now: str | None = None,
    source: str = "unknown",
) -> Summary:
    """Upserta salonger som en körning i ingest_run. `now` kan sättas i tester så
    att tidsstämplarna blir förutsägbara. `source` är SalonSource.name."""
    now = now or utc_now()
    summary = Summary()
    # En transaktion: kraschar källan halvvägs sparas ingenting, inte heller
    # körningen. Salongerna pekar då kvar på förra körningen och är ringbara som förut.
    with conn:
        run_id = conn.execute(
            "INSERT INTO ingest_run (source, started_at) VALUES (?, ?) RETURNING id",
            (source, now),
        ).fetchone()["id"]
        # Post n = rad n+1 i en CSV-fil (rubriken är rad 1). Loggen visar
        # postnumret, inte orgnr: för en enskild firma är det ett personnummer.
        for n, raw in enumerate(salons, start=1):
            orgnr = normalize_orgnr(raw.orgnr)
            name = raw.name.strip() if raw.name else ""
            if orgnr is None or not name:
                summary.rejected += 1
                reason = "ogiltigt eller saknat orgnr" if orgnr is None else "saknar namn"
                log.warning("Post %d avvisad: %s", n, reason)
                continue

            cfar = (raw.cfar or "").strip() or None
            params = {f: getattr(raw, f) for f in SALON_FIELDS}
            params.update(
                orgnr=orgnr,
                cfar=cfar,
                name=name,
                registered_at=raw.registered_at.isoformat() if raw.registered_at else None,
                now=now,
                run_id=run_id,
            )

            exists = conn.execute(
                "SELECT 1 FROM salon WHERE orgnr = ? AND IFNULL(cfar, '') = IFNULL(?, '')",
                (orgnr, cfar),
            ).fetchone()
            salon_id = conn.execute(UPSERT_SALON, params).fetchone()["id"]
            if exists:
                summary.updated += 1
            else:
                summary.inserted += 1

            phone = normalize_phone(raw.phone)
            if raw.phone and phone is None:
                summary.invalid_phones += 1
                log.warning("Post %d: ogiltigt telefonnummer sparas inte", n)
            website = (raw.website or "").strip() or None

            for kind, value in (("phone", phone), ("website", website)):
                if value:
                    conn.execute(INSERT_CONTACT, (salon_id, kind, value, now))

        # Räknas i databasen, inte i loopen: en dubblett i källan är en salong.
        seen = _count_seen_in(conn, run_id)
        if seen == 0:
            # Undantaget rullar tillbaka hela transaktionen, även raden i ingest_run.
            raise EmptyRunError("Körningen gav inga giltiga salonger. Inget sparades.")
        previous = conn.execute(
            "SELECT max(id) FROM ingest_run WHERE id < ?", (run_id,)
        ).fetchone()[0]
        # Förra körningens salonger som inte fick det nya id:t. Ingen förra körning
        # (None) ger 0: = NULL är aldrig sant.
        summary.gone = _count_seen_in(conn, previous)
        conn.execute(
            "UPDATE ingest_run SET finished_at = ?, salons = ?, gone = ? WHERE id = ?",
            (utc_now(), seen, summary.gone, run_id),
        )
    return summary


def _count_seen_in(conn: sqlite3.Connection, run_id: int | None) -> int:
    """Salonger vars senaste körning är `run_id`."""
    row = conn.execute("SELECT count(*) FROM salon WHERE last_seen_run = ?", (run_id,))
    return row.fetchone()[0]


# Bara ett nytt värde uppdaterar fetched_at. Ett oförändrat värde hoppas över
# innan det här körs (se ingest_financials).
UPSERT_FINANCIAL = """
INSERT INTO financial_fact (orgnr, period_end, key, value, source_document, fetched_at)
VALUES (:orgnr, :period_end, :key, :value, :source_document, :now)
ON CONFLICT (orgnr, period_end, key) DO UPDATE SET
    value = excluded.value,
    source_document = excluded.source_document,
    fetched_at = excluded.fetched_at
"""


@dataclass
class FinancialSummary:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0


def _financial_problem(raw: RawFinancial, orgnr: str | None) -> str | None:
    """Varför posten avvisas, eller None om den är giltig."""
    if orgnr is None:
        return "ogiltigt eller saknat orgnr"
    if not raw.key or not raw.key.strip():
        return "saknar key"
    # bool är en int i Python, men True är inte ett belopp.
    if raw.value is None or isinstance(raw.value, bool) or not isinstance(raw.value, int):
        return "value saknas eller är inte hela kronor"
    return None


def ingest_financials(
    conn: sqlite3.Connection, facts: Iterable[RawFinancial], now: str | None = None
) -> FinancialSummary:
    """Upserta finansiella fakta. `now` kan sättas i tester, som för ingest."""
    now = now or utc_now()
    summary = FinancialSummary()
    with conn:
        for n, raw in enumerate(facts, start=1):
            orgnr = normalize_orgnr(raw.orgnr)
            problem = _financial_problem(raw, orgnr)
            if problem:
                summary.rejected += 1
                # Postnumret, inte orgnr (personnummer för en enskild firma).
                log.warning("Finansiell post %d avvisad: %s", n, problem)
                continue

            params = {
                "orgnr": orgnr,
                "period_end": raw.period_end.isoformat(),
                "key": raw.key.strip(),
                "value": raw.value,
                "source_document": raw.source_document,
                "now": now,
            }
            old = conn.execute(
                "SELECT value, source_document FROM financial_fact "
                "WHERE orgnr = :orgnr AND period_end = :period_end AND key = :key",
                params,
            ).fetchone()
            if old is not None and tuple(old) == (raw.value, raw.source_document):
                summary.unchanged += 1
                continue

            conn.execute(UPSERT_FINANCIAL, params)
            if old is None:
                summary.inserted += 1
            else:
                summary.updated += 1
    return summary


def financial_candidates(conn: sqlite3.Connection, legal_forms: Sequence[str]) -> list[str]:
    """Orgnr som får sina årsredovisningar hämtade (D25): ringbara enligt
    callable_salon och med en juridisk form i bolagsverket.annual_report_legal_forms.

    Läser vyn vid körningen, så en spärr som importerats efter ingest gäller direkt.
    DISTINCT: ett företag med fyra salonger har en årsredovisning.
    """
    if not legal_forms:
        return []
    # Platshållarna byggs av antalet former, aldrig av data. Koderna går som parametrar.
    placeholders = ", ".join("?" for _ in legal_forms)
    rows = conn.execute(
        f"SELECT DISTINCT orgnr FROM callable_salon WHERE legal_form IN ({placeholders}) "
        "ORDER BY orgnr",
        list(legal_forms),
    )
    return [r["orgnr"] for r in rows]


def stored_fiscal_years(conn: sqlite3.Connection) -> dict[str, set[date]]:
    """Räkenskapsår per orgnr som redan finns i financial_fact. De hämtas inte igen."""
    years: dict[str, set[date]] = {}
    for r in conn.execute("SELECT DISTINCT orgnr, period_end FROM financial_fact"):
        years.setdefault(r["orgnr"], set()).add(date.fromisoformat(r["period_end"]))
    return years
