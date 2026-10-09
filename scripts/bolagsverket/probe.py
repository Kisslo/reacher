"""T1-14: kontrollerar att adapterns antaganden om Bolagsverkets API stämmer.

Kör från repots rot, med ett aktiebolags orgnr (10 siffror):
    uv run --env-file .env python scripts/bolagsverket/probe.py 5560000000

Går genom BolagsverketClient, så det är adapterns egen token-, gräns- och
omförsökslogik som testas. Skriver ut fältnamn, JSON-typer, filnamn i zip-filen och
vilka (år, key) parsern hittar, aldrig värden eller nycklar. Råa svar sparas i
bolagsverket_raw/ (gitignorerad): committa dem aldrig.
"""

import json
import sys
from pathlib import Path

from reacher.sources.bolagsverket import BolagsverketClient, newest_first, report_files
from reacher.sources.config import (
    BOLAGSVERKET_CLIENT_ID,
    BOLAGSVERKET_CLIENT_SECRET,
    SourcesConfig,
    api_key,
)
from reacher.sources.ixbrl import parse_annual_report

OUT = Path(__file__).resolve().parents[2] / "bolagsverket_raw"
JSON_TYPE = {str: "string", int: "number", float: "number", bool: "boolean", type(None): "null"}


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


if len(sys.argv) != 2:
    sys.exit("Ange ett orgnr: python scripts/bolagsverket/probe.py 5560000000")
orgnr = sys.argv[1].replace("-", "")
OUT.mkdir(exist_ok=True)
settings = SourcesConfig.load().bolagsverket
client = BolagsverketClient(api_key(BOLAGSVERKET_CLIENT_ID), api_key(BOLAGSVERKET_CLIENT_SECRET))

r = client.request("POST", "/dokumentlista", {"identitetsbeteckning": orgnr})
print("dokumentlista: HTTP", r.status, r.headers.get("content-type"))
(OUT / "dokumentlista.json").write_bytes(r.body)
if r.status != 200:
    sys.exit("Ingen dokumentlista. Fel orgnr, eller ett företag utan digitala rapporter?")
seen: dict[str, set[str]] = {}
for path, t in types(json.loads(r.body)):
    seen.setdefault(path, set()).add(t)
for path, ts in seen.items():
    print(f"  {path}: {' | '.join(sorted(ts))}")

documents = newest_first(client.documents(orgnr))
print(f"\n{len(documents)} räkenskapsår:", ", ".join(str(d.period_end) for d in documents))
if not documents:
    sys.exit("Inga dokument att ladda ner.")

newest = documents[0]
r = client.request("GET", f"/dokument/{newest.document_id}")
print(f"\ndokument: HTTP {r.status} {r.headers.get('content-type')}, {len(r.body)} byte")
(OUT / "dokument.bin").write_bytes(r.body)
files = report_files(r.body, newest.document_id)
print(f"{len(files)} xhtml-fil(er) i dokumentet")
for xhtml in files:
    facts = parse_annual_report(xhtml, settings.tag_map, newest.document_id)
    print("Hittade (år, key):", sorted((str(f.period_end), f.key) for f in facts))
    missing = set(settings.tag_map.values()) - {f.key for f in facts}
    if missing:
        print("SAKNAS i rapporten:", sorted(missing), "- kontrollera taggnamnet i tag_map")

print(f"\nKlart. Råa svar i {OUT}. Committa dem aldrig.")
