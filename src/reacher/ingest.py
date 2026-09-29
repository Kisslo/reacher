"""Ingest (T1-04): normalisera RawSalon och upserta i salon + contact.

Regler:
- Ogiltigt eller saknat orgnr -> avvisas. Utan orgnr går salongen inte att spärra.
- Ogiltigt telefonnummer -> salongen sparas, numret gör det inte.
- Alla fält skrivs över, även med NULL. Säger SCB inte längre något om
  ad_status ska den gamla koden inte ligga kvar: då failar vyn öppet.
- E-post sparas aldrig (dataminimering, Format 1). RawSalon har inget sådant fält.
- Signaler skrivs inte: signal-tabellen används inte i MVP:n (D4).
"""

import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from reacher.db import now as utc_now
from reacher.normalize import normalize_orgnr, normalize_phone
from reacher.sources.base import RawSalon

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
    "ad_status",
    "workplace_ad_status",
)

# Byggs av konstanta kolumnnamn, aldrig av data. Värdena går som parametrar.
# ON CONFLICT pekar på uttrycksindexet salon_identity, så att cfar = NULL
# matchar cfar = NULL. first_seen_at finns inte i DO UPDATE och behåller därför
# sitt första värde.
UPSERT_SALON = f"""
INSERT INTO salon (orgnr, cfar, {", ".join(SALON_FIELDS)}, first_seen_at, last_seen_at)
VALUES (:orgnr, :cfar, {", ".join(":" + f for f in SALON_FIELDS)}, :now, :now)
ON CONFLICT (orgnr, IFNULL(cfar, '')) DO UPDATE SET
    {", ".join(f"{f} = excluded.{f}" for f in SALON_FIELDS)},
    last_seen_at = excluded.last_seen_at
RETURNING id
"""

# DO NOTHING: found_at behåller första gången vi såg numret, så en omkörning
# ändrar ingenting.
INSERT_CONTACT = """
INSERT INTO contact (salon_id, kind, value, found_at) VALUES (?, ?, ?, ?)
ON CONFLICT (salon_id, kind, value) DO NOTHING
"""


@dataclass
class Summary:
    inserted: int = 0
    updated: int = 0
    rejected: int = 0
    invalid_phones: int = 0


def ingest(conn: sqlite3.Connection, salons: Iterable[RawSalon], now: str | None = None) -> Summary:
    """Upserta salonger. `now` kan sättas i tester så att tidsstämplarna blir förutsägbara."""
    now = now or utc_now()
    summary = Summary()
    # En transaktion: kraschar källan halvvägs sparas ingenting.
    with conn:
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
    return summary
