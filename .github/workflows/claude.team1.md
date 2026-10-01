# Team 1: Data collection and filtering

Loaded together with the shared `claude.md`. That file holds the handoff formats, decisions (D1–D17) and compliance rules, and it always wins over this one. You are talking to a member of Team 1.

## Our job in one sentence
Turn raw register data into clean, deduplicated `salon` + `contact` rows, and own the `callable_salon` view that decides who may be called at all.

## What we own
| Area | Where |
|---|---|
| Source contract (`RawSalon`, `RawSignal`, `SalonSource`) | `src/reacher/sources/base.py` (merged in PR #6) |
| Source adapters: CSV and SCB (old API now, new API later, D5) | `src/reacher/sources/` |
| Ingest: normalisation + upsert into `salon` / `contact` | `reacher ingest`, `reacher load-seed` |
| Fixtures + hidden ground truth | `tests/fixtures/` |
| Migrations that touch `salon`, `contact`, `signal` | `src/reacher/migrations/` |
| Exclusions: the `callable_salon` view | a migration + its tests |

## What we hand over and what we consume
- **We deliver (Format 1):** `salon` + `contact` rows with raw facts only. We never compute points or thresholds (D6).
- **We consume (Format 2):** the `suppression` table that Team 2 writes. We don't write `opt_out` or `existing_customer`. We read them in `callable_salon`.
- **Team 2 is blocked by us on:** T1-05 (`callable_salon`), in review. Prioritise it.

## Working rules for Team 1
- **Normalise at ingest, not in the source.** Sources yield data exactly as the register delivers it. That way every source gets the same cleaning, and fixtures can be as messy as reality.
  - orgnr: 10 digits, with the dash and the "16" prefix removed.
  - phone: E.164 via `phonenumbers` (region SE).
  - An invalid orgnr is rejected, never inserted.
- **Fail closed on compliance.** Unknown F-skatt/VAT/employer status for a sole proprietorship means excluded (D11). When unsure whether a salon may be called, the answer is no. Missing a lead is cheap; calling someone we mustn't is not.
- **Store SCB codes raw (D18).** `legal_form` and the `*_status` fields are TEXT exactly as SCB sends them. Never turn them into booleans or interpret them in the source or at ingest; only `callable_salon` decides. List the **allowed** codes explicitly (`IN ('1','3')`), never `!= '0'`, so an unknown or new code fails closed.
- **Reklamspärr and NIX are different rules, but one SCB field carries both.** The Reklam code's 1st digit is reklamspärr and its 2nd digit is the phone block / NIX-Telefon. Check it on both company and workplace level.
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
| T1-01 | [#9](https://github.com/Kisslo/reacher/issues/9) | Land the source contract with compliance fields | 2 | In progress (branch `T1-01-compliance-fields`) |
| T1-02 | [#10](https://github.com/Kisslo/reacher/issues/10) | Migration 002: compliance fields on salon | 2 | Todo |
| T1-08 | [#11](https://github.com/Kisslo/reacher/issues/11) | Document the SCB old-API variables | 2 | Done ([docs/scb-fields.md](../../docs/scb-fields.md)) |
| T1-03 | [#12](https://github.com/Kisslo/reacher/issues/12) | Realistic fixture data + ground truth | 2 | Todo, **Team 2 waits on this** |
| T1-04 | [#13](https://github.com/Kisslo/reacher/issues/13) | CSV ingest and load-seed | 3 | Todo |
| T1-05 | [#14](https://github.com/Kisslo/reacher/issues/14) | `callable_salon` view | 3 | In review |
| J-03 | [#21](https://github.com/Kisslo/reacher/issues/21) | End-to-end demo script | 4 | In review (branch `J-03-end-to-end-demo-script`) |
| T1-06 | [#22](https://github.com/Kisslo/reacher/issues/22) | SCB adapter (old API) | 5 | Blocked: credentials |
| J-04 | [#24](https://github.com/Kisslo/reacher/issues/24) | First real list to salespeople | 6 | Todo |
| T1-07 | [#23](https://github.com/Kisslo/reacher/issues/23) | Handle salons that disappear from SCB | 7–8 | Todo |

The GitHub issues are the source of truth (labels `team-1`/`team-2`/`joint`, milestones per week). Keep the Status column roughly in sync at the end of each session.

## Team status
*Session 2 (2026-09-28)*
- **Done:** F3 source contract merged (PR #6). Team 2 approved the D18 field types (2026-09-28).
- **In progress:** T1-01 (#9) on branch `T1-01-compliance-fields`.
- **Blocked:** credentials for SCB's old API.
- **Next up:** T1-01 PR → T1-02 (#10, migration 002) → T1-08 (#11, the SCB PDF answers most of it) → T1-03 (#12, fixtures using real SCB codes).

## Team 1 open questions
- When do the SCB credentials arrive? If not by the end of week 4, escalate.
- Still to verify against a real SCB response (T1-06): see the checklist in docs/scb-fields.md. SNI codes confirmed: 96210, 96220.
- ~~The three rules T1-05 (#14) needs (Reklam codes, Arbetsgivarstatus `2`, unknown legal form).~~ Resolved as D20, D21 and D22 in the shared file.

