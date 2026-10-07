# Team 1: Data collection and filtering

Loaded together with the shared `claude.md`. That file holds the handoff formats, decisions (D1–D31) and compliance rules, and it always wins over this one. You are talking to a member of Team 1.

## Our job in one sentence
Turn raw register data from SCB and Bolagsverket into clean, deduplicated `salon`, `contact` and `financial_fact` rows, and own the `callable_salon` view that decides who may be called at all.

## What we own
| Area | Where |
|---|---|
| Source contract (`RawSalon`, `RawSignal`, `SalonSource`; `RawFinancial` from T1-12) | `src/reacher/sources/base.py` |
| Source adapters: CSV, SCB new API (D24), Bolagsverket (D25) | `src/reacher/sources/` |
| Source config: SNI codes, municipalities, annual-report legal forms, iXBRL tag map | `sources.yaml` (T1-11) |
| Ingest: normalisation + upsert into `salon` / `contact` / `financial_fact` | `reacher ingest`, `reacher load-seed`, `reacher fetch-financials` (T1-14) |
| Fixtures + hidden ground truth; anonymised recorded API responses | `tests/fixtures/` |
| Migrations that touch `salon`, `contact`, `signal`, `financial_fact` | `src/reacher/migrations/` |
| Exclusions: the `callable_salon` view | a migration + its tests |
| SCB field documentation | `docs/scb-fields.md` (T1-09) |

## What we hand over and what we consume
- **We deliver (Format 1):** `salon`, `contact` and `financial_fact` rows with raw facts only. We never compute points or thresholds (D6). Whether a salon is "loss-making" or has "low revenue" is Team 2's call (D26).
- **We consume (Format 2):** the `suppression` table that Team 2 writes. We don't write `opt_out` or `existing_customer`. We read them in `callable_salon`.
- **Team 2 is waiting on us for:** T1-12 (`financial_fact` + fixtures) for T2-09. The `anstKl` answer is in `docs/scb-fields.md` (T1-09).

## Working rules for Team 1
- **Normalise at ingest, not in the source.** Sources yield data exactly as the register delivers it. That way every source gets the same cleaning, and fixtures can be as messy as reality.
  - orgnr: 10 digits, with the dash and the "16"/"19"/"20" prefix removed.
  - phone: E.164 via `phonenumbers` (region SE).
  - An invalid orgnr is rejected, never inserted.
- **Fail closed on compliance.** Unknown F-skatt/VAT/employer status for a sole proprietorship means excluded (D11). When unsure whether a salon may be called, the answer is no. Missing a lead is cheap; calling someone we mustn't is not.
- **Store SCB codes raw (D18).** Codes are TEXT exactly as SCB sends them. The new API mixes JSON strings and numbers: convert with `str()` and nothing else. Only `callable_salon` interprets them. List the **allowed** codes explicitly (`IN ('1')`), never `!= '2'`, so an unknown or new code fails closed.
- **The one deny-list is estates (D30).** Write it NULL-safe: `(legal_form IS NULL OR legal_form NOT IN ('91'))`. A plain `NOT IN` would also exclude every salon with a missing legal form, which D22 handles separately.
- **The compliance view lands before the adapter.** T1-10 merges before or together with T1-06. Otherwise every SCB salon is excluded.
- **Address and municipality come from the workplace (D19).** In the new API, `postAdress` and `kommunSate` belong to the legal unit; for a sole proprietorship that is the owner's home. Since Adress is now in the Excel file (D27), this is a privacy rule, not just a data-quality rule.
- **SCB first, Bolagsverket second (D25).** Only fetch annual reports for companies that pass `callable_salon` and have a legal form that files annual reports. Never re-download a fiscal year already in `financial_fact`.
- **Financial facts as reported.** Whole SEK, sign kept. A missing value is no row, never 0.
- **Config over code (D29).** SNI codes, municipalities and iXBRL tag names live in `sources.yaml`. Adding a municipality or a financial field should not need a code change.
- **API keys stay local (D24).** In `.env` (gitignored), never in code, config, tests, logs, GitHub secrets, issues or a pasted error message. `.env.example` lists the names only. Run with `uv run --env-file .env reacher ...`. If a key is ever committed, rotate it: deleting the commit is not enough.
- **Real API responses are personal data.** Don't commit them or paste them into issues and PRs. Recorded test responses are anonymised by hand (fictional names, Luhn-valid fake orgnr, fake addresses and phones). `API_context.md` stays local.
- **No orgnr, no row.** A salon without an orgnr can't be blocked, so it can't enter the database.
- **Ingest is idempotent.** Running it twice gives the same database. Keep `first_seen_at` and update `last_seen_at`.
- **Data minimisation:** don't store email (Format 1).
- **No network in tests.**
- **Fixtures are fictional:** made-up names, orgnr, phone numbers and financials.
- Changes to `salon`/`contact`/`financial_fact` columns change Format 1. Tell Team 2 and log them in the shared file.

## Tickets
| ID | Issue | Title | Week | Status |
|---|---|---|---|---|
| T1-01 | [#9](https://github.com/Kisslo/reacher/issues/9) | Land the source contract with compliance fields | 2 | Done |
| T1-02 | [#10](https://github.com/Kisslo/reacher/issues/10) | Migration 002: compliance fields on salon | 2 | Done |
| T1-08 | [#11](https://github.com/Kisslo/reacher/issues/11) | Document the SCB old-API variables | 2 | Closed, but the file on main is empty → T1-09 |
| T1-03 | [#12](https://github.com/Kisslo/reacher/issues/12) | Realistic fixture data + ground truth | 2 | Done |
| T1-04 | [#13](https://github.com/Kisslo/reacher/issues/13) | CSV ingest and load-seed | 3 | Done |
| T1-05 | [#14](https://github.com/Kisslo/reacher/issues/14) | `callable_salon` view | 3 | Done |
| J-03 | [#21](https://github.com/Kisslo/reacher/issues/21) | End-to-end demo script | 4 | Done |
| J-05 | [#44](https://github.com/Kisslo/reacher/issues/44) | Update shared context for SCB new API and Bolagsverket | 3 | Done |
| T1-09 | [#46](https://github.com/Kisslo/reacher/issues/46) | Document the SCB new API fields | 4 | Done |
| T1-11 | [#48](https://github.com/Kisslo/reacher/issues/48) | Source config and local API keys | 4 | Todo |
| T1-10 | [#47](https://github.com/Kisslo/reacher/issues/47) | New-API compliance fields, estates and active status | 4 | Todo |
| J-06 | [#45](https://github.com/Kisslo/reacher/issues/45) | Excel: add Adress, Ort, Omsättning, Resultat | 4 | Todo, Team 1 signs off the Adress rule |
| T1-06 | [#22](https://github.com/Kisslo/reacher/issues/22) | SCB adapter (new API) | 5 | Todo |
| T1-12 | [#49](https://github.com/Kisslo/reacher/issues/49) | `financial_fact` table, source shape and fixtures | 5 | Todo, **Team 2 waits on this** |
| T1-13 | [#50](https://github.com/Kisslo/reacher/issues/50) | Parse revenue and result from annual reports (iXBRL) | 5 | Todo |
| J-04 | [#24](https://github.com/Kisslo/reacher/issues/24) | First real list to salespeople | 6 | Todo |
| T1-14 | [#51](https://github.com/Kisslo/reacher/issues/51) | Bolagsverket adapter | 6 | Blocked: Bolagsverket context |
| T1-07 | [#23](https://github.com/Kisslo/reacher/issues/23) | Handle salons that disappear from SCB | 7–8 | Todo |

The GitHub issues are the source of truth (labels `team-1`/`team-2`/`joint`, milestones per week). Keep the Status column roughly in sync at the end of each session.

## Team status
*Session 2026-10-07*
- **Done:** source contract, compliance fields, fixtures, CSV ingest, `callable_salon`, end-to-end demo, J-05, T1-09 (SCB new API documented, recorded responses in `tests/fixtures/scb/`).
- **Blocked:** T1-14 on Bolagsverket API context.
- **Next up:** T1-11 → T1-10 → T1-06 → T1-12 → T1-13.

## Team 1 open questions
See the shared open questions in `claude.md`. T1-09 answered the workplace endpoint, the `anstKl` scale, SCB Omsättning and the JSON types (`docs/scb-fields.md`). New from T1-09: workplace status (`aeStat`), Bolagsverket status (`bolStat`), phone coverage and the owner's name, see the shared open questions.
