# Team 1: Data collection and filtering

Loaded together with the shared `claude.md`. That file holds the handoff formats, decisions (D1–D17) and compliance rules, and it always wins over this one. You are talking to a member of Team 1.

## Our job in one sentence
Turn raw register data into clean, deduplicated `salon` + `contact` rows, and own the `callable_salon` view that decides who may be called at all.

## What we own
| Area | Where |
|---|---|
| Source contract (`RawSalon`, `RawSignal`, `SalonSource`) | `src/reacher/sources/base.py` (branch `F3-source-contract`, on hold per D14) |
| Source adapters: CSV and SCB (old API now, new API later, D5) | `src/reacher/sources/` |
| Ingest: normalisation + upsert into `salon` / `contact` | `reacher ingest`, `reacher load-seed` |
| Fixtures + hidden ground truth | `tests/fixtures/` |
| Migrations that touch `salon`, `contact`, `signal` | `src/reacher/migrations/` |
| Exclusions: the `callable_salon` view | a migration + its tests |

## What we hand over and what we consume
- **We deliver (Format 1):** `salon` + `contact` rows with raw facts only. We never compute points or thresholds (D6).
- **We consume (Format 2):** the `suppression` table that Team 2 writes. We don't write `opt_out` or `existing_customer`. We read them in `callable_salon`.
- **Team 2 is blocked by us on:** T1-03 (fixtures) and T1-05 (`callable_salon`). Prioritise them.

## Working rules for Team 1
- **Normalise at ingest, not in the source.** Sources yield data exactly as the register delivers it. That way every source gets the same cleaning, and fixtures can be as messy as reality.
  - orgnr: 10 digits, with the dash and the "16" prefix removed.
  - phone: E.164 via `phonenumbers` (region SE).
  - An invalid orgnr is rejected, never inserted.
- **Fail closed on compliance.** Unknown F-skatt/VAT/employer status for a sole proprietorship means excluded (D11). When unsure whether a salon may be called, the answer is no. Missing a lead is cheap; calling someone we mustn't is not.
- **Reklamspärr and NIX are different things.** Both apply (exclusion #4 and #3).
- **No orgnr, no row.** A salon without an orgnr can't be blocked, so it can't enter the database.
- **Ingest is idempotent.** Running it twice gives the same database. Keep `first_seen_at` and update `last_seen_at`.
- **Data minimisation:** don't store email (Format 1).
- **No network in tests.** SCB tests use a recorded response, anonymised because a sole proprietorship's data is personal data. Credentials go in `.env` (already gitignored), never in code.
- **Fixtures are fictional:** made-up names, orgnr and phone numbers.
- Changes to `salon`/`contact` columns change Format 1. Tell Team 2 and log them in the shared file.

## Tickets
| ID | Issue | Title | Week | Status |
|---|---|---|---|---|
| J-01 | [#7](https://github.com/Kisslo/reacher/issues/7) | Sign off handoff formats | 2 | Todo, Monday |
| T1-01 | [#9](https://github.com/Kisslo/reacher/issues/9) | Land the source contract with compliance fields | 2 | Waiting on D14 |
| T1-02 | [#10](https://github.com/Kisslo/reacher/issues/10) | Migration 002: compliance fields on salon | 2 | Todo |
| T1-08 | [#11](https://github.com/Kisslo/reacher/issues/11) | Document the SCB old-API variables | 2 | Todo, can start now |
| T1-03 | [#12](https://github.com/Kisslo/reacher/issues/12) | Realistic fixture data + ground truth | 2 | Todo, **Team 2 waits on this** |
| T1-04 | [#13](https://github.com/Kisslo/reacher/issues/13) | CSV ingest and load-seed | 3 | Todo |
| T1-05 | [#14](https://github.com/Kisslo/reacher/issues/14) | `callable_salon` view | 3 | Todo, **Team 2 waits on this** |
| J-03 | [#21](https://github.com/Kisslo/reacher/issues/21) | End-to-end demo script | 4 | Todo |
| T1-06 | [#22](https://github.com/Kisslo/reacher/issues/22) | SCB adapter (old API) | 5 | Blocked: credentials |
| J-04 | [#24](https://github.com/Kisslo/reacher/issues/24) | First real list to salespeople | 6 | Todo |
| T1-07 | [#23](https://github.com/Kisslo/reacher/issues/23) | Handle salons that disappear from SCB | 7–8 | Todo |

The GitHub issues are the source of truth (labels `team-1`/`team-2`/`joint`, milestones per week). Keep the Status column roughly in sync at the end of each session.

## Team status
*Session 1 (2026-09-25)*
- **Done:** nothing beyond the foundation.
- **In progress:** F3 source contract on branch `F3-source-contract`, pushed but no PR (on hold, D14).
- **Blocked:** credentials for SCB's old API.
- **Next up:** J-01 on Monday → T1-08 and T1-02 → T1-01 (once D14 is lifted) → T1-03.

## Team 1 open questions
- When do the SCB credentials arrive? If not by the end of week 4, escalate.
- The old API's variable list (T1-08): legal-form codes, the reklamspärr representation, the SNI version.
- When is the hold on F3 lifted? T1-03 → T1-05 wait on it.
