# Care Package -- AgileCards, 2026-07-16

Everything from the reconciliation and the council deliberation, in one place, in reading order. Assembled for Drew.

**The one-line result:** the council was asked to settle two architectural fault lines and a strategic pivot. It destroyed all three. What survived is a single uncontested fact and a single recommended action.

> **The engine has 713 passing tests and has never been run against a real backlog. `card_metrics` has zero rows. The merge gate has never been exercised.**
> **Recommendation: run the engine. Nothing else, until it has run.**

---

## The files

| # | File | What it is | Read if |
|---|------|-----------|---------|
| **00** | `00_README.md` | this index | start here |
| **01** | `01_RECONCILIATION_MEMO.md` | who owns what across three parallel sessions, what is duplicate, what survives. **Carries a correction notice: its two headline findings are WRONG and preserved unedited on purpose.** | you want the branch-level facts, or the anatomy of how a confident error reached the top of the package |
| **02** | `02_REVISED_HANDOFF.md` | the board-as-control-plane vision + revised sequencing. **Superseded on FL1/FL2 and on the entire market framing.** | you want the vision as it stood before the council took it apart |
| **03** | `03_AGILE_AGENTIC_RESEARCH.md` | why Agile actually works, and what honestly transfers to agent orchestration. Cited, with STRONGER / WEAKER / DOESN'T-MAP calls and a "do not build this" section. The F1-F12 grounding. | you want the intellectual foundation. **This one survived intact and is still good.** |
| **04** | `04_COUNCIL_PROMPT.md` | the verbatim question posed to the council, preserved so the deliberation can be audited against what was actually asked | you want to check the council was asked fairly |
| **05** | `05_COUNCIL_TRANSCRIPT_FULL.md` | **the raw deliberation.** All three rounds, 13 persona submissions, both orchestrator verification passes, the evolving fault-line ledger. Unedited. Preserved at Drew's explicit request. | you want to see the argument itself rather than its summary |
| **06** | `06_COUNCIL_SYNTHESIS.md` | the Foreman's decision doc: bottom lines, what the debate changed, disagreement map, recommended path, 10 action items, process disclosures | **if you read only one file after this one, read this** |
| **07** | `07_FABLE_BRIEF.md` | the self-contained brief for a fresh max-effort Fable session, including binding token-discipline / delegation rules | you are handing this to Fable |

**Reading order for a human:** 00 -> 06 -> 07. Then 01 and 05 if you want the receipts.
**Reading order for the builder:** 07, with 06 open.

---

## What the council actually changed

The deliberation cost real tokens and its single highest-value output was **demolishing the framing of the document that convened it**. Honest accounting:

**Destroyed:**
- **FL1 (the split-brain card store) does not exist.** The engine has been database-canonical since the chunk 2b cutover (`daemon.py`: "The store is the single source of truth"; `_try_claim` calls `repo.claim_card(...)`, not a file move). The memo sourced its premise from a **status doc** while the audit's own method says status docs are untrusted. The Contrarian caught it in Round 1 by grepping the store package and named a ten-minute check as its flip condition. The check ran. It was right.
- **FL2 (`0001` has no agent-native model) is false of the product.** `store/schema.py` already carries `claimed_by`, `model_used`, `estimated_tokens`/`actual_tokens`, `merge_status`, `verified_at`, `attempt_trace_id`, `pr_url`, `stakes`, `difficulty`, `tenant_id`, plus `card_events`, `card_metrics`, `metric_estimates`, `gate_ramp`. Two sessions independently re-invented a worse version because nobody read the store package.
- **The market framing.** `portal-gameplan-opus` **DR-9** (a Drew ruling, 2026-07-08) already made AgileCards an internal dev-tool, `listedInCatalog=false`. `project_agilecards_agentic_vision` (2026-07-16) calls the pivot *"a dogfooding argument, not a hypothetical market claim."* **Drew's own vision doc disclaimed the market framing eight days before the council litigated it.**
- **The pivot argument as stated.** Invalid (an empty niche is not a valuable one) and, per the Researcher's dated primaries, unsound (Linear 2025-05-20, GitHub 2026-03-26, Jira GA May 2026 all ship agents-as-actors). The Logician's diagnosis: the internal-tool version is valid and **does not use the market premise at all** -- *"a premise whose refutation does not touch the conclusion was never load-bearing, which means the market framing was decoration on a decision Drew already made."*

**Survived and sharpened:**
- **Drew's binding test, repaired by the Logician and adopted:** not "will this design still be standing *unmodified*" (that proves too much and would forbid all incremental development) but **"will future work EXTEND this decision or REVERSE it? Reversal, not modification, is the criterion."**
- **Track L passes that test.** The provider port is deliberately neutral and extensible (`ToolSpec` = name + schema + executor). It is the healthiest architecture in the repo.
- **The research (`03`).** Untouched by the council.

**The finding that outranked everything, and arrived last:** the engine has never been run. Verified three ways: the default store `C:\dev\todo\cards.db` does not exist, the card tree holds zero cards, `CARDS_STORE`/`CARDS_TODO_ROOT` are unset at every scope, and no `cards.db` exists anywhere under `C:\dev`. The `LedgerWriter` is correctly wired (7 call sites) -- it has simply never had anything to write about. *(Only surviving caveat: BROOKFIELD, the second machine, could not be checked. That is action item 1.)*

---

## The recommended path (from `06`)

- **Step 0.** Check BROOKFIELD. Fix the CI gates (`continue-on-error: true` on the live board's 96 tests; auth suite not required -- the audit's highest-ROI item, still open). Rescue Branch B's ~15 uncommitted files to a throwaway branch.
- **Step 1.** Rehearsal: throwaway repo, fabricated cards, `--pr-gate` on. **Pass condition: the gate is observed blocking a bad card and `card_metrics` is non-empty. If it fails, stop.**
- **Step 2.** Seed run: low-stakes real repo (not `paradigm-agilecards`), 5-10 tier-1/2 deterministic-AC cards, `--pr-gate` on, Drew reviews all in one sitting. This is the KL5 seed and a live gate test in one run.
- **Step 3.** Everything re-plans against those rows. Not before.

**Out of scope, killed on evidence:** Postgres, `org_id`, RLS, tenancy (no tenants, and a ruling says there will be none -- run the engine's SQLite). Both backend branches (Branch A goes **on ice, intact** -- its RLS is real and correct and is for a customer who does not exist). Any parallel card schema. The board behind `CardRepository` (2-of-10 table overlap). The measurement dashboard (rows before dashboards). The Phase 0-4 arc (calendar cosplay; the unit is PRs against review capacity).

---

## The three things that are still genuinely open

1. **The review-bandwidth risk is unpriced.** A working engine produces agent PRs Drew must read, and review capacity is his binding constraint. **Success makes his bottleneck worse.** Nobody in the council had a number. `card_metrics.human_review_wall_seconds` is instrumented for exactly this; the seed run is the first chance to measure it.
2. **Whether the quoting story survives contact with data.** `project_throughput_economics` says the ledger exists for "realistic job quoting for Paradigm Coding Solutions ('days of churn, not months')." That is the real commercial object, not the board. At zero rows it is, in the User-proxy's words, *"vibes with a schema attached."*
3. **`storage_substrate_v2.md` is referenced ~20 times across the engine and is not on `main`** (unmerged on `origin/design/storage-substrate-v2`, 1 ahead / 81 behind, since 2026-05-19). **The design doc the entire store rests on was never merged.** That is the proximate cause of this whole fiasco: three sessions could not read the design, so they trusted a stale status doc instead. Merge it or delete it.

---

## Process honesty

- Council ran `/council deep`: 3 rounds, 13 persona submissions, **validation gate 5/5 PASS** in the rebuttal round (no re-fires, no NON-RESPONSIVE flags). Researcher had live web tools and used them. User-proxy read memory successfully (DR-9 is quoted, not inferred).
- **Two personas withdrew their own headline claims on their own evidence.** The Contrarian killed the proposal it had authored one round earlier. The Researcher corrected itself in both directions (Vibe Kanban did not die -- **Bloop** died; the project went Apache-2.0 and the niche refilled within weeks, which is a demand signal, not a graveyard).
- **Unresolved caveats carried honestly:** no DB-dependent test on either backend branch was actually executed (no live Postgres in the audit environment), so claims about Branch A's RLS rest on reading its code, not running it. The BROOKFIELD store check is outstanding.
- **The process that produced the two false fault lines is still the process.** The reconciliation session could not read other sessions' transcripts and did not grep the engine's store package before making the highest-stakes call in the package. Two council rounds were spent undoing a ten-minute check nobody ran. **Verify against `main` before a decision ships, not after.**
