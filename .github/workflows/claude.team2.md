# Team 2: Scoring, output and feedback

Loaded together with the shared `claude.md`. That file holds the handoff formats, decisions (D1–D19) and compliance rules, and it always wins over this one. You are talking to a member of Team 2.

## Our job in one sentence
Take the callable salons, rank them, give each salesperson a locked Excel file, read the outcomes back in, and prove every Friday that the top 20 converts better than the rest.

## What we own
| Area | Where |
|---|---|
| Scoring (incl. deriving `registered_recently` and `small_employer`, D6) | `scoring.yaml`, `src/reacher/config.py`, the scoring module |
| Ranking, per-salesperson split, snapshot | `reacher build-lists`, tables `call_list` / `call_list_row` |
| Excel contract, export and import | `src/reacher/excel/`, `reacher import-outcomes`, table `outcome` |
| Block list writes (`opt_out`, `existing_customer`) | table `suppression` |
| Outcome simulation (dev only) | T2-05 |
| Weekly report: top 20 vs the rest | `reacher report` |

## What we consume and what we hand over
- **We consume (Format 1):** eligible `salon` rows through Team 1's `callable_salon` view, with phone details from the related `contact` row. Never select from `salon` directly when building lists, or exclusions get bypassed. Migration 004 exposes `salon.*`; `city` is the Excel "Område" value (D19).
- **We deliver (Format 2):** `suppression` rows. "Spärra" → `opt_out`, "Registrerad" → `existing_customer` (D13).
- **Until Team 1 delivers:** build against your own mock data in exactly the Format 1 shape. T1-03 fixtures and the T1-05 `callable_salon` view are now available in migrations.

## Working rules for Team 2
- **"Spärra" means never again.** `opt_out` rows are never deleted, never rebuilt from Excel, and survive a later import that changes the same row's Utfall. This is a legal requirement (IMY), not a feature.
- **Match imported rows on `row_id` only,** and validate `_meta` (`call_list_id`, `week`) so an old or wrong file is rejected.
- **Unknown Utfall values are reported,** never dropped silently.
- **`contract.py` is the single source for the Excel format.** Changing it means telling both teams first, because it's a joint change (see J-02).
- **No orgnr in any Excel file** (D12).
- **Tuning lives in `scoring.yaml`,** never in code. Bump `version` when you change weights, since `call_list.scoring_version` records which weights produced a list.
- **Freeze what was sent.** `call_list_row` stores the score, reasons and phone as they were. Later data changes must not rewrite history.
- **The simulation (T2-05) must never touch a real list.** It refuses any file containing salons that aren't in the fixtures.
- **Report honestly.** Always show the sample size next to a hit rate. With a handful of calls, differences are noise.

## Tickets
| ID | Issue | Title | Week | Status |
|---|---|---|---|---|
| J-01 | [#7](https://github.com/Kisslo/reacher/issues/7) | Sign off handoff formats | 2 | Done |
| J-02 | [#8](https://github.com/Kisslo/reacher/issues/8) | Remove Orgnr column from the Excel contract | 2 | Done |
| T2-01 | [#15](https://github.com/Kisslo/reacher/issues/15) | Scoring from salon facts | 2 | Done |
| T2-03 | [#16](https://github.com/Kisslo/reacher/issues/16) | Excel export per the contract | 3 | Done |
| T2-02 | [#17](https://github.com/Kisslo/reacher/issues/17) | Rank, split per salesperson, freeze the snapshot | 3 | Done |
| T2-04 | [#18](https://github.com/Kisslo/reacher/issues/18) | Import outcomes from returned xlsx | 4 | Done |
| T2-05 | [#19](https://github.com/Kisslo/reacher/issues/19) | Simulated outcomes for demos | 4 | Done |
| T2-06 | [#20](https://github.com/Kisslo/reacher/issues/20) | Report: top 20 vs the rest | 4 | In progress |
| J-03 | [#21](https://github.com/Kisslo/reacher/issues/21) | End-to-end demo script | 4 | Todo |
| J-04 | [#24](https://github.com/Kisslo/reacher/issues/24) | First real list to salespeople | 6 | Todo |
| T2-07 | [#25](https://github.com/Kisslo/reacher/issues/25) | First tuning pass on signal weights | 7–8 | Todo |

The GitHub issues are the source of truth (labels `team-1`/`team-2`/`joint`, milestones per week). Keep the Status column roughly in sync at the end of each session.

## Team status
*Session 4 (2026-09-30). T2-02, T2-04 and T2-05 are complete; T1-03 fixtures are available.*
- **Done:** Handoff formats signed off, Orgnr removed from the Excel contract, scoring from salon facts, deterministic ranking and salesperson splitting with frozen snapshots, contract-compliant Excel export, outcome import with permanent suppressions, and fixture-only seeded outcome simulation for demos.
- **In progress:** T2-06 (weekly report: top 20 vs. the rest).
- **Next up:** Implement the weekly conversion report using the imported outcomes and show sample sizes beside hit rates.

## Team 2 open questions
- How is "reached" defined in the report: are "Ej nådd" rows excluded from the denominator? (T2-06)
- ~~Where does the 24-month threshold for `registered_recently` live?~~ Resolved (T2-01): in `scoring.yaml` under `thresholds`, because it's tuning. `registered_recently_months: 24` counts calendar months, inclusive, from the day the list is built. `small_employer_classes: ["2"]` holds SCB size-class codes as quoted text (`"2"` = 1–4 employees; `"0"` = data missing, which never counts). Weights version bumped to `v2`.
- Rows are split fairly and deterministically by alternating global rank (T2-02 implementation default).
