"""Letar efter värden från de riktiga SCB-svaren i det som är stagat för commit.

Kör från repots rot efter git add:  uv run python scripts/scb/leakcheck.py
Träffar som STOCKHOLM eller ett datum är ofarliga. Ett namn, en adress, ett
telefonnummer eller ett orgnr är det inte: byt ut det innan du committar.
"""

import json
import subprocess
from pathlib import Path


def scalars(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from scalars(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from scalars(v)
    elif isinstance(obj, (str, int)) and not isinstance(obj, bool) and len(str(obj)) >= 6:
        yield str(obj)


real: set[str] = set()
for path in (Path(__file__).resolve().parents[2] / "scb_raw").glob("*.json"):
    if not path.name.startswith("kodtabell_"):  # kodtabeller är inte personuppgifter
        real |= set(scalars(json.loads(path.read_text(encoding="utf-8"))))

staged = subprocess.run(
    ["git", "diff", "--cached"], capture_output=True, text=True, encoding="utf-8", check=True
).stdout
hits = sorted(v for v in real if v in staged)
print(f"{len(real)} värden från riktiga svar, {len(hits)} finns i det stagade:")
for v in hits:
    print("  ", v)
