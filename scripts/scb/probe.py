"""T1-09: anrop mot SCB:s nya API som kontrollerar docs/scb-fields.md.

Kör från repots rot:  uv run --env-file .env python scripts/scb/probe.py

Sparar råa svar i scb_raw/ (gitignorerad, committa dem aldrig). Skriver bara ut
fältnamn, JSON-typer, format och kodtabeller, aldrig värden från ett företag eller
arbetsställe: för en enskild firma är de personuppgifter.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://apiafr.scb.se/v1"
OUT = Path(__file__).resolve().parents[2] / "scb_raw"  # repots rot / scb_raw (gitignorerad)
OUT.mkdir(exist_ok=True)
MUNICIPALITY = "0180"
CODE_TABLES = [
    "anstklkoder", "reklamsparrtypkoder", "telefonsparrtypkoder", "ftgstatkoder",
    "jurformkoder", "fskattstatkoder", "momsstatkoder", "arbgivstatkoder",
    "aestatkoder", "bolstatkoder", "omsklkoder", "naringsgrenkoder",
]  # fmt: skip
JSON_TYPE = {str: "string", int: "number", float: "number", bool: "boolean", type(None): "null"}

KEY = os.environ.get("SCB_API_KEY")
if not KEY:
    sys.exit("SCB_API_KEY saknas. Lägg den i .env och kör med: uv run --env-file .env ...")


def get(path: str, **params):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(url, headers={"X-API-Key": KEY, "Accept": "application/json"})
    time.sleep(1)  # Gränsen är inte dokumenterad. Var snäll.
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        # problem+json innehåller aldrig nyckeln, så felet går att skriva ut.
        sys.exit(f"HTTP {e.code} på {path}: {e.read().decode('utf-8', 'replace')}")


def save(name: str, data) -> None:
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def types(obj, prefix=""):
    """(sökväg, JSON-typ) för varje fält, rekursivt. Inga värden."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from types(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        yield prefix, "array"
        for item in obj:
            yield from types(item, prefix + "[]")
    else:
        yield prefix, JSON_TYPE[type(obj)]


def report(title: str, records: list) -> None:
    seen: dict[str, set[str]] = {}
    for record in records:
        for path, t in types(record):
            seen.setdefault(path, set()).add(t)
    print(f"\n== {title} ({len(records)} poster): fält -> JSON-typ")
    for path, ts in seen.items():
        print(f"  {path}: {' | '.join(sorted(ts))}")


def mask(value) -> str:
    """Visar formatet utan innehållet: 070-123 45 67 -> 999-999 99 99."""
    return re.sub(r"\d", "9", value) if isinstance(value, str) else repr(value)


print("api-info:", get("/api-info"))

print("\n== Kodtabeller")
for table in CODE_TABLES:
    rows = get(f"/kodtabeller/{table}")
    save(f"kodtabell_{table}.json", rows)
    if table == "naringsgrenkoder":
        rows = [r for r in rows if "962" in str(r.get("kod"))]
    print(f"-- {table}")
    for r in rows:
        print(f"   {r.get('kod')!r} ({JSON_TYPE[type(r.get('kod'))]}) {r.get('klartext')}")

sni_codes = [
    str(r["kod"])
    for r in json.loads((OUT / "kodtabell_naringsgrenkoder.json").read_text(encoding="utf-8"))
    if str(r["kod"]).replace(".", "") in ("96210", "96220")
]
print("\nSNI-koder som de skrivs i API:t:", sni_codes)

print("\n== Antal arbetsställen per SNI (hela landet, rangordning 1)")
for sni in sni_codes:
    print(f"   {sni}: {get(f'/arbetsstallen/naringsgren/{sni}/count')}")

page = get(f"/arbetsstallen/naringsgren/{sni_codes[0]}", limit=5000)
save("ae_naringsgren_page1.json", page)
print("\nSida 1, pagination:", page["pagination"])
report("arbetsstallen/naringsgren (partial)", page["arbetsstallen"])
if page["pagination"]["hasMore"]:
    page2 = get(
        f"/arbetsstallen/naringsgren/{sni_codes[0]}",
        limit=5000,
        cursorId=page["pagination"]["nextCursorId"],
    )
    print("Sida 2, pagination:", page2["pagination"])

workplaces = page["arbetsstallen"]
local = [w for w in workplaces if (w.get("belagenhetsadress") or {}).get("kommun") == MUNICIPALITY]
print(f"\nI sida 1 ligger {len(local)} av {len(workplaces)} i kommun {MUNICIPALITY}")
print("Ben saknas (null/tom):", sum(not w.get("ben") for w in workplaces), "st")

# Listan är sorterad på peOrgNr, så alla juridiska personer (16...) kommer före de
# enskilda firmorna (19/20...). Bläddra vidare tills en enskild firma i kommunen dyker upp.
current = page
while current["pagination"]["hasMore"] and not any(
    str(w.get("peOrgNr", ""))[:2] in ("19", "20") for w in local
):
    current = get(
        f"/arbetsstallen/naringsgren/{sni_codes[0]}",
        limit=5000,
        cursorId=current["pagination"]["nextCursorId"],
    )
    local += [
        w
        for w in current["arbetsstallen"]
        if (w.get("belagenhetsadress") or {}).get("kommun") == MUNICIPALITY
    ]
    print("En sida till, pagination:", current["pagination"])

# Ett aktiebolag (16...) och en enskild firma (19/20...) i kommunen, om de finns på sidan.
for label, prefixes in (("ab", ("16",)), ("ef", ("19", "20"))):
    workplace = next((w for w in local if str(w.get("peOrgNr", ""))[:2] in prefixes), None)
    if workplace is None:
        print(f"\nIngen {label} i kommunen, hoppar över")
        continue
    ae = get(f"/arbetsstallen/{workplace['cfarNr']}/full")
    je = get(f"/juridiskaenheter/{workplace['peOrgNr']}/full")
    save(f"ae_full_{label}.json", ae)
    save(f"je_full_{label}.json", je)
    report(f"arbetsstallen/{{cfarNr}}/full ({label})", [ae])
    report(f"juridiskaenheter/{{peOrgNr}}/full ({label})", [je])
    print(f"\n-- Format ({label}), siffror maskerade")
    for name, value in (
        ("peOrgNr", je.get("peOrgNr")),
        ("orgNr", je.get("orgNr")),
        ("ae.tel", ae.get("tel")),
        ("je.tel", je.get("tel")),
        ("ae.startDat", ae.get("startDat")),
        ("je.regDat", je.get("regDat")),
    ):
        print(f"   {name}: {mask(value)}")
    print("   Tid i datumen:", (ae.get("startDat") or "")[10:], (je.get("regDat") or "")[10:])
    print("   ben finns:", bool(ae.get("ben")))
    print("   namn == foretagsnamn:", je.get("namn") == je.get("foretagsnamn"))
    print("   foretagsnamn finns:", bool(je.get("foretagsnamn")))
    print("   kommunSate == arbetsställets kommun:", je.get("kommunSate") == MUNICIPALITY)

print(f"\nKlart. Råa svar i {OUT}. De är personuppgifter: committa dem aldrig.")
