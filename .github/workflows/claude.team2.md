# Team 2: Scoring, output and feedback

Loaded together with the shared `claude.md`. That file holds the handoff formats, decisions (D1–D31) and compliance rules, and it always wins over this one. You are talking to a member of Team 2.

## Our job in one sentence
Take the callable salons, rank them, give each salesperson a locked Excel file, read the outcomes back in, and prove every Friday that the top 20 converts better than the rest.

## What we own
| Area | Where |
|---|---|
| Scoring: signal registry, derived signals (`registered_recently`, `small_employer`, `loss_making`, `low_revenue`), shadow mode | `scoring.yaml`, `src/reacher/config.py`, `src/reacher/scoring.py` |
| Ranking, per-salesperson split, snapshot (incl. derived signals per row, T2-08) | `reacher build-lists`, tables `call_list` / `call_list_row` |
| Excel contract, export and import (incl. Adress, Ort, Omsättning, Resultat, J-06) | `src/reacher/excel/`, `reacher import-outcomes`, table `outcome` |
| Block list writes (`opt_out`, `existing_customer`) | table `suppression` |
| Outcome simulation (dev only) | T2-05 |
| Weekly report: top 20 vs the rest | `reacher report` |

## What we consume and what we hand over
- **We consume (Format 1):** eligible `salon` rows through Team 1's `callable_salon` view, with phone details from the related `contact` row. Never select from `salon` directly when building lists, or exclusions get bypassed. `city` is the Excel "Ort" value (D19, D27). Financial facts come from `financial_fact` through Team 1's latest-financials view (T1-12), joined on orgnr.
- **We deliver (Format 2):** `suppression` rows. "Spärra" → `opt_out`, "Registrerad" → `existing_customer` (D13).
- **Until Team 1 delivers:** build against fixtures in exactly the Format 1 shape. `financials.csv` comes with T1-12.

## Working rules for Team 2
- **"Spärra" means never again.** `opt_out` rows are never deleted, never rebuilt from Excel, and survive a later import that changes the same row's Utfall. This is a legal requirement (IMY), not a feature.
- **Match imported rows on `row_id` only,** and validate `_meta` (`call_list_id`, `week`) so an old or wrong file is rejected.
- **Unknown Utfall values are reported,** never dropped silently.
- **`contract.py` is the single source for the Excel format.** Changing it is a joint change. Import rejects files whose headers don't match, so import every outstanding file before merging a contract change.
- **No orgnr in any Excel file** (D12). **Adress is the workplace address only** (D19, D27): never a sole proprietor's home.
- **Tuning lives in `scoring.yaml`,** never in code. Bump `version` when you change anything, since `call_list.scoring_version` records which config produced a list.
- **Shadow before points (D26).** A new signal starts with `weight: 0`: derived and stored in `call_list_row`, not shown to the salesperson, no points. It gets points only after T2-07 shows it helps, logged as a decision.
- **One registry entry + one yaml block per signal (D29).** Config fails to load if `scoring.yaml` names a signal without a derive function.
- **Missing facts give no signal.** A salon without annual reports is neither loss-making nor profitable.
- **Freeze what was sent.** `call_list_row` stores the score, reasons, derived signals and phone as they were. Later data changes must not rewrite history.
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
| T2-06 | [#20](https://github.com/Kisslo/reacher/issues/20) | Report: top 20 vs the rest | 4 | Done |
| J-03 | [#21](https://github.com/Kisslo/reacher/issues/21) | End-to-end demo script | 4 | Done |
| J-05 | [#44](https://github.com/Kisslo/reacher/issues/44) | Update shared context for SCB new API and Bolagsverket | 3 | Done |
| T2-08 | [#52](https://github.com/Kisslo/reacher/issues/52) | Signal registry, on/off switch and shadow mode | 4 | Done |
| J-06 | [#45](https://github.com/Kisslo/reacher/issues/45) | Excel: add Adress, Ort, Omsättning, Resultat | 4 | Todo |
| T2-09 | [#53](https://github.com/Kisslo/reacher/issues/53) | Financial signals in shadow mode | 5 | Todo, waits on T1-12 |
| J-04 | [#24](https://github.com/Kisslo/reacher/issues/24) | First real list to salespeople | 6 | Todo |
| T2-07 | [#25](https://github.com/Kisslo/reacher/issues/25) | First tuning pass on signal weights (incl. shadow signals) | 7–8 | Todo |

The GitHub issues are the source of truth (labels `team-1`/`team-2`/`joint`, milestones per week). Keep the Status column roughly in sync at the end of each session.

## Team status
*Session 2026-10-06*
- **Done:** everything through J-03: scoring, ranking and snapshots, Excel export/import, block list, simulation, top-20 report, end-to-end demo.
- **In progress:** J-05 (context update).
- **Next up:** T2-08 → J-06 → T2-09.

## Team 2 open questions
- ~~How is "reached" defined in the report?~~ Resolved as D23.
- ~~Where does the 24-month threshold live?~~ Resolved (T2-01, T2-08): `months` in the `registered_recently` block of `scoring.yaml`.
- Rows are split fairly and deterministically by alternating global rank (T2-02 implementation default).
- **`anstKl` scale** (from T1-09): if the new API uses the SME scale, `classes: ["2"]` in the `small_employer` block is wrong.
- **`low_revenue` threshold:** the starting value is an assumption (weight 0, so it can't affect lists yet). Alternatively SCB's Omsättning size class, if the API exposes it.
- ~~**Excel financials:** which years?~~ Resolved as D27: all available years up to 3, one line per year in the Omsättning and Resultat cells. Final format signed off in J-06.
