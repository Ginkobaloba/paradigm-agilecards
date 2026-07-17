# BRIEF FOR A FRESH FABLE SESSION -- AgileCards

**From:** Opus 4.8 (audit, reconciliation, and council orchestration session), commissioned by Drew Mattick.
**Date:** 2026-07-16
**Status:** this brief is **self-contained**. You can execute from this file alone. The rest of the care package (`00`-`06`, same directory) is depth, not prerequisite. **This brief SUPERSEDES** the earlier "build the real backend" brief and the `docs/briefs/FABLE_BRIEF_agentic-dev-must-have.md` in PR #55.
**Every factual claim below was verified against `origin/main` @ `7c9ce99`.** Where this brief and any doc in the repo disagree, verify against `main` and trust the code.

---

## 0. Read this part twice

**Do not build a schema. Do not build a dashboard. Do not build a control plane. Not yet.**

A six-persona deep council was convened on the question "should AgileCards commit to the board-as-control-plane pivot, and what is the right sequencing." It destroyed the question. Both headline architectural fault lines in the briefing package were **verified false**. The market framing that motivated the whole vision was **disclaimed by Drew's own vision doc eight days before the council sat**. Every candidate first move was killed by the council, mostly by the persona who proposed it.

One fact survived uncontested, and it is your entire job:

> **The engine has 713 passing tests and has never been run against a real backlog. `card_metrics` has zero rows. The merge gate has never been exercised.**

Verified: the engine's default card store is `sqlite:<todo-root>/cards.db` with todo-root defaulting to `C:\dev\todo` (`cli/__main__.py:315-321`, `types.py:268`). **`C:\dev\todo\cards.db` does not exist.** `C:\dev\todo\` holds zero cards (0 files in `backlog/`, `active/`, `done/`, `blocked/`, `amendments/`). `CARDS_STORE` and `CARDS_TODO_ROOT` are unset at process, User, and Machine scope. No `cards.db` exists anywhere under `C:\dev`. No `C:\dev\todo-store`. The `LedgerWriter` **is** correctly wired (7 call sites in `daemon.py`), so the ledger *would* populate. It never has.

*(One caveat survives: Drew has a second machine, BROOKFIELD, which could not be checked. If BROOKFIELD holds a populated `cards.db`, this brief's premise collapses and you should stop and tell Drew. That check is your first action.)*

The engine is not a well-tested system. It is a well-tested **set of parts**, tested against fixtures its own authors wrote. Nobody has watched it run. **The distance between those two things is the entire alpha**, and one run closes more of it than any amount of architecture work.

---

## 1. Your mission, in order

**Run the engine. Nothing else, until it has run.**

### Step 0 -- clear the decks (an afternoon, mostly delegable)

1. **Check BROOKFIELD for a populated `cards.db`.** One minute. If it exists, stop and report; this brief's premise is void.
2. **Fix the CI gates.** `.github/workflows/ci.yml` runs the live board backend's 96 tests with `continue-on-error: true` (green even when they fail; only `tsc` gates), and the FastAPI auth/org-isolation suite is **not a required status check**. This is why "1,100 tests green" is a number and not a guarantee. The audit called it the highest-ROI item in the entire document and it is still open. Remove the `continue-on-error`; make the auth suite required.
3. **Rescue Branch B's uncommitted work.** The worktree `C:\dev\_worktrees\backend-real` (branch `feat/cards-api-postgres-rls`) holds roughly 15 files of *uncommitted* implementation (all of `cards_api/routers/`, `models.py`, `repository.py`, `wire.py`, `db.py`, `audit.py`, a Dockerfile, 5 test files). It is one `git clean` from gone. Commit it to a throwaway branch. You are not going to use it; you are preventing an accident.

### Step 1 -- the rehearsal (hours). THIS IS A GATE.

Point the engine at a **throwaway repo with fabricated cards**, `--pr-gate` **on**.

**Pass condition, verbatim from the council and non-negotiable: the gate is observed BLOCKING A BAD CARD, and `card_metrics` is non-empty.**

Why a throwaway and not something real: `--pr-gate` has never been exercised. If you point the first live run at a repo Drew cares about, that run is simultaneously *the first test of the safety control* and *the first thing the safety control is protecting*. That ordering is backwards. Rehearse where nothing is at stake.

**If the gate does not block a bad card: STOP. Do not proceed to Step 2. Report to Drew.** That result would mean the gate is not a defaults problem, it is a control that does not work, and the "machine-checked AC gate makes cheap agents safe" thesis (the load-bearing claim under this whole product) is unsupported. That is the single most important thing you could discover, and discovering it is a success, not a failure.

### Step 2 -- the seed run (one sitting). ONLY IF STEP 1 PASSED.

Point the engine at a **low-stakes real repo**. Suggested: `paradigm-ops` or a scratch fork. **Explicitly NOT `paradigm-agilecards`** -- do not let it eat itself.

- **5 to 10 cards**, tier-1/2, **all-deterministic AC** (the verifier's deterministic handlers cost zero LLM tokens; subjective AC would drag in the Claude cascade and muddy the measurement).
- `--pr-gate` **on**.
- Drew reviews all of them, in one sitting. That cap is deliberate: it is a mitigation for the risk in §3.

This single run is simultaneously the **KL5 seed** (already locked and scoped by Drew: "do not declare the MVP done until this number exists") and a live gate test. Two locked deliverables, one run.

**Instrument `card_metrics.human_review_wall_seconds`.** It already exists in the schema and it measures the one thing nobody has a number for (§3).

### Step 3 -- stop and report.

Everything else re-plans against what those rows say. **Do not proceed past Step 2 without Drew.** Not the board surface, not Postgres, not the dashboard, not Track S.

---

## 2. What is explicitly OUT of scope, and why

Each of these was killed by the council on evidence. Do not resurrect them without new facts.

- **Postgres, `org_id`, RLS, multi-tenancy.** `portal-gameplan-opus` **DR-9**, a Drew ruling dated **2026-07-08**: *"Agile Cards STAYS Vite+Python blessed internal dev-tool NOT a customer SKU"*, `listedInCatalog=false`. Corroborated by `project_agilecards_agentic_vision` (2026-07-16): the pivot is *"a dogfooding argument, not a hypothetical market claim."* **There are no tenants and a ruling says there will be none.** Run the engine's SQLite. Multi-tenancy for zero tenants is not "done properly from the beginning," it is building the wrong thing carefully.
- **Both backend branches.** `feat/backend-postgres-rls` (Branch A) is **good work** -- it found and fixed a real packaging bug, fails loudly rather than skipping to defeat CI-masking, expanded ruff and gated mypy, and built genuine `ENABLE`+`FORCE ROW LEVEL SECURITY` with fail-closed policies. **Its RLS is real and correct and it is for a customer who does not exist.** Put it **on ice, intact** -- preserve the branch, document it as the thaw-on-first-tenant asset. Do **not** delete it. Do **not** build on it. Do **not** merge it with Branch B (they collide on 5 files including duelling `0001` migrations; a hand-merge lands on the RLS policy itself).
- **A parallel card schema of any kind.** The engine already has the card model. `store/schema.py` `CARD_COLUMNS` carries `tenant_id` (first PK on every table), `claimed_by`, `attempt_trace_id`, `model_used`, `last_heartbeat`, `merge_status`, `verified_at`, `verified_by`, `estimated_tokens`, `actual_tokens`, `story_hash`, `trace_id`, `pr_url`, `work_type`, `stakes`, `difficulty`, plus `card_events` (with `actor_id`/`actor_type`), `dependencies`, `card_metrics`, `metric_estimates`, `gate_ramp`. **Two sessions independently re-invented a worse version of this because nobody read the store package.** Do not be the third.
- **Putting the board behind `CardRepository`.** Tempting and wrong. Engine tables (8): `cards, card_events, batches, dependencies, counters, card_metrics, metric_estimates, gate_ramp`. Board tables (10): `cards, card_rank, card_events, saved_views, sprints, sprint_cards, retros, story_batches, staged_cards, audit_events`. **Overlap: 2.** The engine has zero sprint/retro/saved-view code and never will. The seam survives only as a future note: *if* the board ever needs engine-authored `cards`/`card_events`, read them through the port, read-only.
- **The measurement dashboard (F2/F7/F9/F11/F12).** Its own author withdrew the pricing: *"I priced a wired writer as if it were data. It is plumbing over an empty table."* The ledger needs **rows before it needs a dashboard**. Also: per-trace cost attribution is commodity in 2026 (Langfuse, Cursor admin analytics, Anthropic's Enterprise Analytics API, March 2026). The only distinctive thing is **cost joined to rework-rate and contract-survival at the card grain** -- and that join is meaningless until the gate is real. It is downstream of Step 1, not parallel to it.
- **The market/category framing entirely.** Linear shipped agent assignment 2025-05-20, GitHub shipped agent session status in Issues/Projects 2026-03-26, Jira's Rovo agents went GA May 2026. The shallow layer is occupied. (The deep layer -- worktree, diff, per-card spend, machine-checked gate verdict on the card -- is genuinely empty, but that is irrelevant: **AgileCards is not being sold.**) Competitors do not matter for a tool you build for yourself.
- **The "Phase 0-4" arc.** Vetoed. Phases are calendar cosplay. The unit is **PRs against tiered review capacity**, and any phase boundary that needs Drew is the boundary that stalls when the IEP cycle or the legal case eats a week.

---

## 3. The risk nobody priced, and it is yours to measure

**A working engine produces agent PRs Drew must read. Review capacity is his binding constraint. Success here makes his bottleneck worse.**

Nobody in the council had a number for this. The 5-10 card cap in Step 2 is a mitigation, not an answer. `card_metrics.human_review_wall_seconds` is instrumented for exactly this question -- use it.

**The kill condition, from the User-proxy simulating Drew:** *if the first 10 rows show human-review minutes per merged PR above his current hand-coded rate, the quoting story dies and Cards is a toy he dogfoods.* The threshold is admittedly guessed because no data exists. Your run produces the first data that could set it.

There is a broader pattern the User-proxy named against Drew's own memory, and it is worth carrying: `nexus-outperform-program` / `nexus-state-lookover-2026-07-10` shows the same shape -- flagship scored 0/50 on real oracle-graded tasks, the fabric was never run live. In its words: *"I have a pattern of building the engine and admiring it."* This brief exists to break that pattern, not extend it.

---

## 4. TOKEN DISCIPLINE -- binding, not advisory

**Fable runs roughly 2x Opus per token. Token efficiency is prioritized over speed for this build.** Drew said so explicitly, and the council upheld it as a veto.

**Delegate mechanical, well-specified, low-judgment subtasks to lighter model sub-agents. Reserve Fable-level reasoning for genuinely ambiguous architectural and design calls.**

Concretely, for this brief:

| Delegate to a lighter model (Sonnet or below) | Keep at Fable level |
|---|---|
| The BROOKFIELD store check | Deciding whether a gate failure is a defect or a config error |
| CI workflow edits (removing `continue-on-error`, marking a check required) | Interpreting what the first 10 rows actually mean |
| Committing Branch B's stray files to a throwaway branch | Judging whether the run's result kills or supports the quoting story |
| Fabricating the throwaway repo's test cards | Any call that changes the plan |
| Reading logs, tallying pass/fail, collating `card_metrics` rows | Deciding whether to stop or proceed at the Step 1 gate |
| Writing the mechanical parts of the run report | The honest verdict in the run report |

**This is not a novel ask -- it is the house pattern.** The K/L/S/P session runs it (Drew locked "L -> S -> P, sequential, Fable-level agent, no fan-out" -- *tracks* stay sequential, but mechanical subtasks inside a track still delegate down). Dispatch itself runs it: this very care package was produced by an Opus orchestrator that fanned six audit lenses, a research agent, a branch-verification agent (deliberately run at Sonnet, because it was mechanical), and eleven council personas out to sub-agents, keeping only synthesis and judgment at the top. Cite that precedent if anyone asks. **The reconciliation between "no fan-out" and "delegate mechanical work" is: do not fork the track order, do delegate the keystrokes.**

**Anti-pattern to avoid specifically:** Fable doing mechanical re-typing at 2x Opus. That was an explicit User-proxy veto. If you catch yourself hand-writing boilerplate, config, or repetitive test scaffolding, stop and delegate it.

---

## 5. Hard constraints (non-negotiable)

- **Git discipline.** Every index-touching git command runs from **Windows PowerShell**, never a Linux/WSL sandbox against `C:\dev\` (it corrupts `.git/index.lock`; this repo has been bitten). Never push to `main` directly. Never `--force`. Never `--legacy-peer-deps`. Never change repo visibility. Never bypass the verify gate. `delete_branch_on_merge` stays ON; stacked PRs merge bottom-up.
- **Worktree isolation.** One worktree per parallel agent. This repo has hit HEAD corruption from shared checkouts, and **other sessions are live right now** -- treat their worktrees (`.claude/worktrees/feat-backend-postgres-rls` is locked, `_worktrees/backend-real`, `_worktrees/paradigm-agilecards/*`) as read-only and not yours to prune.
- **Tier-3 is Drew-gated.** Anything touching auth, the compliance seams, or the merge/deploy path: open a PR and stop. Agent self-merge is future-only.
- **Verify against `main`, not against docs.** This is the lesson of the whole package. The reconciliation memo's two headline findings were both false because it trusted a status doc. Two personas caught it in Round 1 by grepping the store package. **`storage_substrate_v2.md` is referenced ~20 times across the engine and is NOT on `main`** (it sits unmerged on `origin/design/storage-substrate-v2`, 1 ahead / 81 behind, 2026-05-19). The design doc the entire store rests on was never merged. That is the proximate cause of this whole fiasco. Merging or deleting it is a real action item.
- **Style.** No em dashes anywhere (use `--`, parentheses, or commas). No sugarcoating in docs or handoffs. Truth over comfort is a hard rule, not a tone preference.
- **Session protocol.** Read `C:\dev\SESSION_PROTOCOL.md` and the project `CLAUDE.md` at start; run `vstart`; write a handoff and run `vend` at the end.

---

## 6. Definition of Done for this brief

You are done when **all** of these are true:

1. The BROOKFIELD store caveat is resolved one way or the other.
2. CI no longer reports green when the live board's tests fail, and the auth/isolation suite is a required check.
3. Branch B's uncommitted work is committed somewhere recoverable.
4. **Either** the rehearsal gate was observed blocking a bad card and `card_metrics` is non-empty, **or** you stopped and reported that it did not.
5. **If the gate passed:** 5-10 real cards ran on a low-stakes repo with `--pr-gate` on, Drew reviewed them, and there are **real rows in `card_metrics`** including `human_review_wall_seconds`.
6. A short, honest run report exists in-repo saying what actually happened, what the rows say, and whether the quoting story ("days of churn, not months") is supported, dead, or still unknown at N=10. **If N=10 is too noisy to say, say that** -- the Expansionist's read is that it flips to meaningful only past ~30 cards with the gate enforcing, and ten rows will tell you whether the machine runs, which is a different and currently more urgent question.
7. Branch A is on ice, intact and documented, not deleted and not built on.

**You are NOT done by building anything.** If this brief ends with a new schema, a dashboard, or a control plane, it failed.

---

## 7. Pointers (depth, not prerequisite)

Same directory (`docs/care-package/`):
- `00_README.md` -- what each file is and the order to read them
- `01_RECONCILIATION_MEMO.md` -- who owns what, the branch facts, **plus a correction notice: its two headline findings are wrong and preserved on purpose**
- `02_REVISED_HANDOFF.md` -- the vision, **superseded on FL1/FL2 and on the market framing**
- `03_AGILE_AGENTIC_RESEARCH.md` -- why Agile works and what honestly transfers to agent orchestration (the F1-F12 grounding; still good, still mostly not your job yet)
- `04_COUNCIL_PROMPT.md` -- the verbatim question posed to the council
- `05_COUNCIL_TRANSCRIPT_FULL.md` -- the raw deliberation, all three rounds, preserved at Drew's request
- `06_COUNCIL_SYNTHESIS.md` -- the Foreman's decision doc. **If you read only one supporting file, read this one.**

In-repo: `docs/audits/AUDIT_2026-07-16_alpha-gap-list.md` (branch `audit/alpha-gap-list-2026-07-16`, PR #54) -- the verified four-tier gap list. Note its Decision-1 recommendation (ship legacy Express) is **dead**; Drew ruled the opposite.

Engine: `engine/runner/README.md`, `engine/runner/src/cards_runner/store/README.md`, `engine/RUNNER_CONTRACT.md`, `engine/DEFINITION_OF_DONE.md`.

External: `C:\dev\AGILECARDS_MVP_LOCALGPU_ASSESSMENT_2026-07-14.md` -- the K/L/S/P track doc. **Contains the stale "card files are the source of truth" claim that caused this whole mess. Correct it or flag it.**
