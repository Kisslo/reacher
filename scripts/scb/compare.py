"""Jämför fältnamn och JSON-typer (aldrig värden) mellan ett riktigt svar och en fixture.

Kör från repots rot:
    uv run python scripts/scb/compare.py scb_raw/ae_full_ef.json tests/fixtures/scb/ae_full.json
"""

import json
import sys
from pathlib import Path

JSON_TYPE = {str: "string", int: "number", float: "number", bool: "boolean", type(None): "null"}


def types(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from types(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        yield prefix, "array"
        for item in obj:
            yield from types(item, prefix + "[]")
    else:
        yield prefix, JSON_TYPE[type(obj)]


def shape(path: str) -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {}
    for p, t in types(json.loads(Path(path).read_text(encoding="utf-8"))):
        seen.setdefault(p, set()).add(t)
    return seen


real, fixture = shape(sys.argv[1]), shape(sys.argv[2])
problems = 0
for path in sorted(real.keys() | fixture.keys()):
    r, f = real.get(path, set()), fixture.get(path, set())
    # null i det ena och ett värde i det andra är ok: anonymiseringen får välja.
    if (r - {"null"}) != (f - {"null"}) and r - {"null"} and f - {"null"}:
        print(f"TYP   {path}: riktigt {sorted(r)}, fixture {sorted(f)}")
        problems += 1
    elif not r or not f:
        print(f"SAKNAS {path}: {'bara i riktiga svaret' if r else 'bara i fixturen'}")
        problems += 1
print("Samma form." if problems == 0 else f"{problems} skillnader.")
