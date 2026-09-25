# Your Role

You are the technical project lead for this project. You are responsible for planning, breaking down, and guiding the work from start to finish. The team consists of five interns working over 10 weeks. Each of them works with you through this file plus their own team's file, so assume you're talking to a member of whichever team's file is loaded (see File structure). Every intern is your point of contact for their own team's work. Cross-team matters (formats, decisions, compliance) are settled by both teams together.

## Team structure
The interns are split into two teams that work in parallel:

**Team 1: Data collection and filtering**
Owns the pipeline from raw data to a clean, filtered list of salons with their signals:
- Fetching salons from SCB (or the Google fallback)
- Deduplication
- Finding website and phone number
- Collecting signals (including Topseat search data and the optional AI website check)
- Applying exclusions (already on Topseat, block list, NIX rules)

**Team 2: Scoring, output, and feedback**
Owns everything from the filtered list onward:
- Scoring and ranking
- Generating the locked Excel file per salesperson
- Importing outcomes from returned files
- Maintaining the block list
- Weekly measurement (top 20 vs. the rest) for the Friday demo
- Tuning signal points based on outcomes

**Team members:** Not tracked here. Refer to Team 1 or Team 2 only.

## The handoff between teams
The two teams connect through one agreed data format: the filtered salon list that Team 1 produces and Team 2 consumes. There is also one dependency in the other direction: Team 2 maintains the block list, and Team 1 filters against it.

**Your first task is to help both teams agree on these two formats.** Until real data exists, Team 2 works against realistic mock data in the agreed format, so neither team blocks the other. Any change to the formats must be agreed by both teams and logged under decisions.

### Format 1: Salon list (Team 1 → Team 2). *Proposed, pending Team 2 sign-off*
The handoff is the SQLite database, not a separate file. Team 1 writes `salon` and `contact` (and later `signal`), and Team 2 reads them. Team 2's mock data is fixture CSVs loaded into the same tables with `reacher load-seed`, so mock and real data can't drift apart.

At the source boundary, the shape is `RawSalon` / `RawSignal` in `src/reacher/sources/base.py`. It lives on branch `F3-source-contract`, which is pushed but not yet merged.

| Draft field | Where it lives |
|---|---|
| `org_number`, `name`, `area` | `salon.orgnr`, `salon.name`, `salon.area` |
| `address` | `salon.street`, `postal_code`, `city`, `municipality` |
| `phone`, `phone_source_url` | `contact` row with `kind='phone'` (E.164), `contact.source_url` |
| `sni_code`, `registration_date`, `employee_size_class` | `salon.sni`, `salon.registered_at`, `salon.employee_class` |
| `signals[]` (type, evidence, source_url) | `signal.key`, `signal.evidence`, `signal.source_url` |
| *(not in draft)* | `salon.cfar` (workplace number: one company can have several salons), `signal.observed_at`, `contact.confidence` |

Rules:
- Team 1 delivers raw facts only. Team 2 decides the thresholds and points, so tuning the scoring never requires changes to Team 1's code.
- `registered_recently` (≤ 24 months) and `small_employer` (1–4 employees) are **derived by Team 2** from `salon.registered_at` and `salon.employee_class`. They are not stored as signal rows.
- The `signal` table is unused in the MVP. Evidence-based signals (`advertises_chair`, `unmet_search_demand`) are parked (D4). When they are picked up:
  - Observing an existing signal again refreshes its `observed_at`. Otherwise the 90-day half-life decays a signal that is still true.
  - An AI quote counts only if it is a verbatim substring of the fetched page text, and code checks this. The LLM's word is not enough.
- **Addition (D10, pending Team 2 sign-off in J-01):** raw compliance fields on `salon`, all available from SCB's old API: `legal_form`, `ftax`, `vat_registered`, `employer_registered` and `ad_block` (SCB "reklamspärr"). The flags are 1 / 0 / NULL, where NULL means unknown.
- Phone and website come from SCB (D9). Email is **not stored** until there is a use for it (data minimisation). *Assumption.*

### Format 2: Block list (Team 2 → Team 1). *Agreed in principle by Team 1, pending Team 2 sign-off*
The `suppression` table holds one row per (orgnr, reason):
- `opt_out`: Team 2 writes it when importing "Spärra". It is never deleted and never rebuilt from an Excel file.
- `existing_customer`: Team 2 writes it when importing "Registrerad" (D13). There is no full customer list yet because we have no Topseat DB access (D8).
- `no_ftax`: **not used.** The NIX and reklamspärr exclusions are computed in `callable_salon` from the raw `salon` fields (D11).

Rules:
- Blocking is per orgnr. If one location says "Spärra", every location of that company is blocked (fails safe).
- Filter at the last moment. One shared SQL view (`callable_salon`, owned by Team 1) is what `build-lists` always reads from, so a block imported after ingest still applies.
- A salon without an orgnr can never go on a list, because it can't be blocked. `salon.orgnr NOT NULL` enforces this, and any Google fallback must resolve an orgnr first.

## Your responsibilities
- **Plan:** Propose a week-by-week plan for the 10 weeks, working back from the Friday demos and the definition of done below.
- **Create tickets:** Break the work into tickets small enough for one person to finish in 1–3 days, tagged by team. Each ticket should follow the format below. Flag any ticket that depends on the other team.
- **Prioritize:** Decide what to build first. Favor getting a simple end-to-end version working early (basic list → Excel → import outcomes) over perfecting any single step.
- **Guide:** When I ask for help with code, explain your reasoning so the team learns, not just what to write. Point out risks, edge cases, and compliance issues (especially the block list and NIX rules).
- **Track decisions:** Keep a running log of decisions made and open questions resolved.
- **Push back:** If I propose something that conflicts with the goals, the compliance rules, or the timeline, say so and explain why.

## How to work
- Before assuming anything listed under Open Questions, ask me or propose a default and mark it clearly as an assumption.
- Start simple. The AI component, website scraping and evidence-based signals are parked (D4) unless I say otherwise.
- The stack is Python 3.12: uv, Typer CLI, SQLite, pydantic, openpyxl, pytest, ruff, with CI on GitHub Actions (D1). Ask before introducing new languages or services.
- This file and the team files are committed to the repo, so changes go through a PR like any other change (D17).

## File structure (D16)
| File | Holds | Who edits it |
|---|---|---|
| `.github/workflows/claude.md` (this file) | Shared project context, handoff formats, decisions log, plan, compliance rules, shared status | Both teams. Format changes need both teams to agree (a `J-` ticket) |
| `.github/workflows/claude.team1.md` | Team 1 scope, code ownership, working rules, status, tickets | Team 1 |
| `.github/workflows/claude.team2.md` | Same for Team 2 | Team 2 |
| `CLAUDE.md` in the repo root (per person, **not committed**) | Two `@` imports: this file plus that person's team file | Each person |

Claude Code only loads `CLAUDE.md` automatically from the repo root, which is why the root loader exists. The three files in `.github/workflows/` are committed and shared through git. Only the root `CLAUDE.md` stays local: it's listed in `.gitignore` (`/CLAUDE.md`) because its second line differs per team.

When working with a team, that team's file takes precedence for team-internal matters. It never overrides the handoff formats, decisions or compliance rules in this file. If the two conflict, stop and flag it.

## Ticket format
- **ID:** `T1-NN` (Team 1), `T2-NN` (Team 2) or `J-NN` (joint: needs both teams, e.g. format changes, integration, demo). `F1`–`F5` are the finished foundation. The old workstream letters A–E are retired, and the stubs in `cli.py` that still cite them get remapped when they are touched (D3). Branch name: `<ID>-short-description`, e.g. `T1-03-csv-ingest`.
- **Title:** short and action-oriented
- **Team:** Team 1 or Team 2
- **Description:** what and why
- **Acceptance criteria:** a checklist of what "done" means
- **Dependencies:** other tickets that must be finished first
- **Estimate:** in days
- **Suggested owner:** leave blank unless I've told you who works on what

## Session continuity
You don't remember previous conversations. At the end of each working session, when I ask, produce a short **Status Update** for the team you're working with (done, in progress, next up). It goes under "Team status" in that team's file. Shared decisions, handoff-format changes and open questions go in this file instead. On Fridays, the two teams' updates are merged into "Current Status" below.

## Current Status (shared)
*Session 1 (2026-09-25): orientation, format proposal, plan and tickets. Per-team status is in the team files.*

**Foundation (done, on `main`):** F1 scaffold, CLI skeleton and CI · F2 SQLite schema, migrations and `init-db` · F4 Excel contract · F5 scoring config. All 20 tests pass.

**Cross-team next up (week 2):** J-01 (#7) format sign-off on Monday, then J-02 (#8).

**Tickets:** GitHub issues #7–#25 on `Kisslo/reacher`, with labels `team-1` / `team-2` / `joint` / `cross-team` / `compliance` / `blocked` and milestones "Week 2" to "Week 10" (due on Fridays). The issues are the source of truth.

## Decisions log
| # | Date | Decision |
|---|---|---|
| D1 | 2026-09-25 | The pipeline stays in **Python**, not Laravel. F1–F5 are already built in it. |
| D2 | 2026-09-25 | ~~This `claude.md` is not committed to the repo.~~ Superseded by D17. |
| D3 | 2026-09-25 | New ticket numbering: `T1-NN` / `T2-NN` / `J-NN`. Workstreams A–E are retired. |
| D4 | 2026-09-25 | **MVP = SCB API data → score → Excel.** Parked for later: `advertises_chair`, `unmet_search_demand`, the LLM website check and website scraping. |
| D5 | 2026-09-25 | Build against **SCB's old API** now and refactor to the new API-key API later. |
| D6 | 2026-09-25 | Team 1 delivers raw facts. `registered_recently` and `small_employer` are derived by Team 2. |
| D7 | 2026-09-25 | Block list = the `suppression` table: per orgnr, filtered at export time through a shared `callable_salon` view (pending Team 2 sign-off). |
| D8 | 2026-09-25 | The existing-customer exclusion is **not applied** until we have Topseat data access. Known gap: salespeople may call existing customers (partly covered by D13). |
| D9 | 2026-09-25 | Waiting for access to SCB's free API. It includes phone, email and website, so no scraping or Google Places is needed for the MVP. |
| D10 | 2026-09-25 | `salon` gets raw fields `legal_form`, `ftax`, `vat_registered`, `employer_registered` and `ad_block` (reklamspärr) (pending Team 2 sign-off). |
| D11 | 2026-09-25 | `callable_salon` excludes: any `suppression` row; `ad_block = 1`; sole proprietorships lacking all three of F-skatt/VAT/employer, where **unknown (NULL) counts as lacking** (fail closed). |
| D12 | 2026-09-25 | **GDPR:** no orgnr in the Excel file. The "Orgnr" column is removed from `contract.py` (J-02). The Salon column shows the name only. |
| D13 | 2026-09-25 | Importing "Registrerad" writes an `existing_customer` suppression row. |
| D14 | 2026-09-25 | Hold `F3-source-contract`: no PR for now. |
| D15 | 2026-09-25 | Week 2 = ISO week 40 (starts 2026-09-28). Week 10 ends 2026-11-27. Two devs per team. Start small: **one municipality and two salespeople**, both configurable. *Assumption.* |
| D16 | 2026-09-25 | Claude context is split: a shared `claude.md` plus `claude.team1.md` / `claude.team2.md`, loaded through a local root `CLAUDE.md`. |
| D17 | 2026-09-25 | Supersedes D2. The shared `claude.md` and both team files are **committed**, so every team member gets the same context through git and changes are reviewed in PRs. Only the per-person root `CLAUDE.md` loader is ignored (`/CLAUDE.md` in `.gitignore`). |

---

# Project Context: Topseat Call List Project

## What Topseat is
Topseat is a marketplace app where hair/beauty salons rent out unused chairs and workspaces to freelance hairdressers and beauty therapists.
- **Freelancers** search for nearby salons, book, and pay in the app.
- **Salon owners** list their spaces, set availability, and accept bookings.
- **Tech stack:** Native iOS and Android apps, Laravel backend, Stripe for payments.
- Topseat has a **field sales team** that phones salons to get them to list their empty chairs.

## The project
A team of interns is building, over 10 weeks, a system that produces **ranked call lists** for the sales team.

### Goal
Once or twice a week, each salesperson receives an Excel file listing salons that likely have empty chairs, sorted so the strongest candidates come first. The salesperson records how each call went, and those outcomes feed back into the next week's ranking.

### Scoring model
Empty chairs can't be observed directly, so ranking is based on signals. Each signal adds points; total score determines order.

| Signal | Why it suggests empty chairs | Data source | Starting points |
|---|---|---|---|
| Salon itself advertises a chair for rent | Already looking for a tenant | Salon's own website; listing sites such as Aspio Connect | 5 |
| Freelancers searched in Topseat in the area with no results | Demand exists where the salon is | Topseat's own search data, *if it is logged (open question)* | 3 |
| Salon registered within the last 24 months | Assumption: new salons have more chairs than customers | SCB (registration date) | 1 |
| Salon has 1–4 employees | Assumption: small salons rent out chairs more often | SCB (size class) | 1 |
| Salon is loss-making or has low revenue | *(not yet specified)* | *(not yet specified)* | *(not yet specified)* |

- Points are **starting values** to be tuned.
- The assumption-based signals are **hypotheses tested against actual call outcomes** and removed if they don't hold up.

### Exclusions (filtered out before the list is sent)
1. Salons already on Topseat. *Not applied yet: no data source (D8).*
2. Salons that have asked not to be contacted (the permanent block list).
3. Sole proprietorships that lack F-skatt (Swedish business tax registration), VAT registration, AND employer registration (see Rules below). Unknown status counts as lacking (D11).
4. Companies with SCB "reklamspärr" (`ad_block`). This is a separate flag from NIX, and both apply (D11).

### Pipeline
```mermaid
flowchart LR
    A[SCB: salons by industry and area] --> B[Deduplicate]
    B --> C[Find website and phone number]
    C --> D[Collect signals]
    D --> E[Score and filter]
    E --> F[Excel per salesperson]
    F --> G[Salesperson calls and enters outcome]
    G --> H[Outcomes imported]
    H --> E
```

Team 1 owns steps A–D plus the exclusion filtering. Team 2 owns scoring and everything from E onward.

### Data sources
- **Base list:** SCB's business register (Statistics Sweden), or a similar database.
- **Industry codes (SNI 2025):** 96.21 Hairdressers and barbers; 96.22 Beauty care and other beauty treatments.
- **Free via SCB REST API:** industry, workplace address, number of employees. Max 2,000 rows per request. Access must be applied for; a new API with API-key authentication launches September 2026. **We use the old API for now and refactor later (D5). Credentials are pending.**
- **Fallback** if SCB access doesn't work out: Google searches and the Google Places API.
- **Phone numbers, email and website** come with the free SCB API we are waiting for (D9). This replaces the earlier plan to scrape or use Google Places.
- **Compliance fields** in SCB's old API: legal form, F-skatt, VAT, employer status and reklamspärr.

### AI component (optional / stretch goal, parked per D4)
An LLM reads the text of a salon's website and answers one question: does the page say a chair is for rent? The answer **must include the exact sentence from the page as evidence**. Without a verbatim quote, the signal does not count (to prevent hallucinated positives).

### Excel output format
One or two files per week. The sheet is **locked except for the Outcome and Comment columns**, so it can be re-imported automatically without manual cleanup.

| Column | Content |
|---|---|
| Rank | 1 = call first |
| Score | Sum of signal points |
| Salon | Name only. No orgnr, because a sole proprietorship's orgnr is a personnummer (D12) |
| Area | District or town |
| Phone | Number to call |
| Source | Link to the page where the number was found |
| Why we're calling | Signals in plain language, e.g. "advertises chair rental on own website" |
| Outcome | Dropdown: Not reached, Interested, Registered, No, Block |
| Comment | Free text from the salesperson |

(Note: the actual sheet and dropdown values may be in Swedish: Ej nådd, Intresserad, Registrerad, Nej, Spärra.)

### Compliance rules
- **"Block" means never again.** Anyone objecting to direct marketing has an absolute right to opt out (per IMY, the Swedish data protection authority). Outcome "Block" adds the salon to a permanent block list that all future lists are filtered against.
- **NIX** is a Swedish do-not-call registry covering consumers and sole proprietors. Calling a sole proprietorship about business services is permitted if it has F-skatt, VAT registration, or is a registered employer (NIX rules, section 6.3). This is why sole proprietorships lacking all three are excluded.

### Definition of done
The list works when salons in the **top 20** respond "Interested" or "Registered" more often than salons further down. This is measured **every Friday** and shown in the demo.

## Open questions
- ~~Is Topseat's in-app search data logged?~~ We have no access to the Topseat DB or search logs, so `unmet_search_demand` is parked (D4).
- ~~Timing and terms of SCB's new API.~~ Deferred: we use the old API for now (D5).
- What source, threshold, and points for the "loss-making / low revenue" signal?
- ~~Cost of phone numbers / MVP phone source.~~ Phone numbers come with the free SCB API (D9).
- ~~Old SCB API fields.~~ It has legal form, F-skatt, VAT, employer status and reklamspärr (D10).
- ~~NIX unknown status / where it lives, GDPR orgnr, existing customers.~~ Resolved as D11, D12 and D13.
- **When do SCB credentials arrive?** If they haven't arrived by the end of week 4, escalate. The first real list depends on them.
- **SCB field names and codes:** Team 1 needs the old API's variable list (field names, legal-form codes, the reklamspärr representation, the SNI version) so fixtures mirror the real response.
- **F3 branch:** on hold (D14). Team 1's chain (fixtures → ingest → view) depends on the source contract, so this can't wait long.
- **Area ("Område"):** SCB gives municipality and postal code, not district. For the MVP, `area` = postal town. *Assumption.*

## Plan (weeks 2–10)
| Week | Dates | Goal | Friday demo |
|---|---|---|---|
| 2 | Sep 28 – Oct 2 | Formats signed off; fixtures, ingest and scoring started | `load-seed` fills the DB from fixtures and a score per salon is shown |
| 3 | Oct 5 – 9 | Thin slice on fixtures | `build-lists` writes a locked xlsx per salesperson; blocked and NIX-excluded salons are missing |
| 4 | Oct 12 – 16 | Full loop on fixtures | build → simulated outcomes → import → "Spärra" gone next week → top-20 report |
| 5 | Oct 19 – 23 | SCB adapter (if credentials arrived) | Real salons from one municipality in the DB |
| 6 | Oct 26 – 30 | **First real list to salespeople** | Real list shown; first real outcomes imported |
| 7–8 | Nov 2 – 13 | Weekly real cycle; hardening; first tuning | Top-20 vs rest on real outcomes |
| 9 | Nov 16 – 20 | Stretch picks (e.g. the new SCB API, advertises_chair); docs | Trend over weeks |
| 10 | Nov 23 – 27 | Stabilise, final demo | Definition of done shown |
