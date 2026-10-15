# Reacher

Reacher creates ranked call lists for Topseat's field sales team. It imports
salons from SCB, applies contact and compliance filters, optionally fetches
financial facts from Bolagsverket, scores the remaining salons, and exports one
locked Excel file per salesperson.

The weekly loop is:

```text
SCB -> ingest -> callable_salon -> Bolagsverket -> scoring -> Excel
                                      ^                         |
                                      |                         v
                              suppression <- outcomes <- calls
```

The shared handoff between data collection and scoring is the SQLite database.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Python 3.12 (installed automatically by uv)

Install dependencies and run the test suite:

```sh
uv sync
uv run pytest -q
```

## Local API keys

Real SCB and Bolagsverket data require local credentials. Copy
[.env.example](./.env.example)
to `.env` and fill in the values:

```sh
cp .env.example .env                 # bash
Copy-Item .env.example .env          # PowerShell
uv run --env-file .env reacher check-sources
```

`check-sources` validates `sources.yaml` and reports which keys are present. It
does not make network requests or print key values. Never commit `.env`, put
keys in YAML, or disable TLS certificate verification.

## Project configuration

The source scope is configured in
[sources.yaml](./sources.yaml):

- `scb.sni_codes`: SNI 2025 codes to collect
- `scb.municipalities`: workplace municipality codes
- `bolagsverket.annual_report_legal_forms`: legal forms for annual reports
- `bolagsverket.years`: number of latest fiscal years
- `bolagsverket.tag_map`: iXBRL tags mapped to financial fact keys

The scoring model is configured in
[scoring.yaml](./scoring.yaml).
Signal weights, thresholds, enabled/disabled signals, and shadow mode belong in
that file rather than in code. Increase its `version` whenever the model
changes.

## CLI commands

All commands use `reacher.db` by default unless `--db` is supplied.

| Command | Purpose |
| --- | --- |
| `init-db` | Create a database and apply migrations |
| `load-seed` | Load the fictional development fixtures |
| `ingest --source csv --path PATH` | Ingest salons from a CSV fixture directory |
| `ingest --source scb` | Fetch and ingest a complete SCB snapshot |
| `check-sources` | Validate configuration and local API keys without network calls |
| `fetch-financials` | Fetch Bolagsverket facts for eligible companies |
| `build-lists WEEK SELLERS` | Score, rank, and export one workbook per seller |
| `import-outcomes PATH` | Import outcomes from a completed workbook |
| `simulate-outcomes INPUT OUTPUT` | Create reproducible demo outcomes for fixtures |
| `report WEEK` | Compare the top 20 with the rest of the week's calls |

Use `reacher --help` or `reacher COMMAND --help` for all options.

## Real-data workflow

Run SCB ingest before fetching financial facts, and use the same database for
every later step:

```sh
uv run --env-file .env reacher init-db
uv run --env-file .env reacher ingest --source scb
uv run --env-file .env reacher fetch-financials
uv run reacher build-lists 2026w42 "Anna,Bengt"
```

SCB's maintenance window is 04:00–04:30. Each SCB ingest is a full snapshot:
salons missing from the latest successful run remain in the database but are
not callable. A failed or empty run is not saved. The SCB adapter reports the
number of salons collected and how many have a phone number.

On Windows, Bolagsverket may require a system certificate bundle. If the
certificate cannot be verified, open
<https://gw.api.bolagsverket.se> in Edge once or set `SSL_CERT_FILE` in your
local `.env`:

`SSL_CERT_FILE="C:/Program Files/Git/mingw64/etc/ssl/certs/ca-bundle.crt"`

Never turn off certificate verification.

## Filtering and compliance

`build-lists` reads `callable_salon` view; it must not select directly
from the `salon` table. The view fails closed and excludes salons with:

- a suppression for the organisation;
- an advertising or telephone block at company or workplace level;
- an inactive company or workplace;
- legal form `91` (estate);
- an unknown or missing compliance status;
- no valid organisation number;
- no qualifying F-tax, VAT, or employer registration where the legal form may
  represent a sole proprietorship; or
- no record in the latest ingest run.

Addresses and municipalities come from the workplace, not the legal unit.
Phone numbers are normalized during ingest. The organisation number is not
included in Excel files.

## Excel files and outcomes

`build-lists` writes one workbook per salesperson to `output/`. The workbook
contains the rank, score, salon name, workplace address, town, phone, source,
financial facts, and the reasons for calling. Only `Utfall` and `Kommentar`
are editable; the other cells are locked.

Valid outcomes are:

- `Ej nådd`
- `Intresserad`
- `Registrerad`
- `Nej`
- `Spärra`

Import matches rows by their hidden `row_id` and validates the workbook metadata
(`call_list_id`, week, and seller). Old or unrelated workbooks are rejected.
Unknown outcome values are reported instead of being silently discarded.

`Spärra` creates a permanent organisation-level `opt_out` suppression.
`Registrerad` creates an `existing_customer` suppression. Suppressions are
applied when the next list is built, and an opt-out is never removed by a later
Excel import.

## Scoring and financial facts

The default model derives:

- `registered_recently`
- `small_employer`
- `loss_making`
- `low_revenue`
- `declining_revenue`

Signals can be disabled with `enabled: false`. A signal with `weight: 0` is in
shadow mode: it is stored with the list row for later analysis but does not
affect ranking or appear as a reason to call.

Financial facts are read through the `latest_financial_fact` view. Missing
annual reports mean missing facts, not zero revenue or zero result. The current
financial signals are shadow-only.

## Fixture demo

The fixtures in [tests/fixtures/](./tests/fixtures/)
are fictional. They include salon data, financial facts, SCB responses,
Bolagsverket/iXBRL responses, and simulation ground truth. Ground truth is used
only by `simulate-outcomes`, never by ingest or scoring.

Run the complete fixture loop from the repository root:

```sh
uv run reacher init-db --db reacher_fixtures.db
uv run reacher load-seed --db reacher_fixtures.db
uv run reacher build-lists 2026w40 "Anna,Bengt" --db reacher_fixtures.db
uv run reacher simulate-outcomes output/2026w40_Anna.xlsx output/2026w40_Anna_utfall.xlsx --db reacher_fixtures.db
uv run reacher simulate-outcomes output/2026w40_Bengt.xlsx output/2026w40_Bengt_utfall.xlsx --db reacher_fixtures.db
uv run reacher import-outcomes output/2026w40_Anna_utfall.xlsx --db reacher_fixtures.db
uv run reacher import-outcomes output/2026w40_Bengt_utfall.xlsx --db reacher_fixtures.db
uv run reacher build-lists 2026w41 "Anna,Bengt" --db reacher_fixtures.db
uv run reacher report 2026w40 --db reacher_fixtures.db
```

The second week's lists exclude organisations that were marked `Spärra` or
`Registrerad` in the first week. The report shows hit rate and sample size for
the top 20 and the rest. The fixture sample is intentionally small and is for
demonstration, not performance conclusions.

`tests/test_demo.py` runs the same workflow in a temporary directory. If the
demo steps change, update the test together with this section.

## Development checks

Run the focused checks before opening a pull request:

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Database migrations are stored in
[src/reacher/migrations/](./src/reacher/migrations/)
and are applied automatically by the CLI. Do not use real personal data in
fixtures or tests.
