# Reconciliation Memo -- who owns what, what's duplicate, what survives

**Date:** 2026-07-16
**Author:** Opus 4.8 (audit + synthesis session)
**Method:** filesystem and git inspection only. Cross-session chat was not available; nothing here waits on another session's cooperation. Every claim is from `git`/disk or a verified command run. Where I could not verify, I say so.
**Baseline:** `origin/main` @ `7c9ce99`.
**Governing principles applied (Drew, 2026-07-16):**
1. *Done properly from the beginning* -- prefer a clean, correct build over force-salvaging partial/rushed work.
2. *Version increments are evolutions of one codebase, never rewrites.* A design flaw found now gets fixed now; deferred architectural debt compounds into a forced rewrite once features interconnect on top of it.
3. The test for any phase is not "does this work" but **"will this design still be standing, unmodified, three phases from now?"**

---

## 0. Bottom line

Three sessions have been building AgileCards in parallel. Two of them (the duplicate pair) built **the same thing twice**; one of them (K/L/S/P) is on a **genuinely different and mostly complementary** track. The duplication is real but bounded and now stopped. The serious finding is not the duplication -- it is that **two live sessions are building on directly contradictory models of what a card is**, and neither has noticed:

- The **backend branches** make **Postgres the source of truth** and reduce the card's file path to a *derived, synthetic* property.
- The **engine (Track L/S)** is built on the documented premise that **card files on disk are the source of truth**, and the runner claims cards from a filesystem tree.

**Track S -- "sprint → orchestrator wire" -- is precisely where those two models collide.** Building Track S before resolving which store is authoritative would wire together two stores that disagree about truth, and that wire gets rewritten the moment the question is answered. By Drew's principle 2, this is a fix-now, not a log-for-v-next.

Second finding: the backend branches were built to my **audit's narrow P1** (legacy wire parity + Postgres + RLS), not to the **vision's Phase 1** (agent-native schema). Their `0001` migration carries **zero** agent-native model. `0001` is foundational; a schema designed for a superseded brief is the textbook case of principle 2.

---

## 1. What exists -- the three tracks and their real state (verified)

### 1a. The duplicate pair -- "Build real AgileCards backend" (two sessions)
Both were told directly by Drew (confirmed by him, and independently recorded in both branches' ADRs) to **build the real backend rather than ship alpha on legacy Express**. This **supersedes my audit's Decision-1 recommendation (Option A, ship legacy first)**. That recommendation is dead; the record should stop showing it as live.

| | **Branch A** `feat/backend-postgres-rls` | **Branch B** `feat/cards-api-postgres-rls` |
|---|---|---|
| Committed | 3 commits, 42 files, +4592/-184 | 2 commits, ~8-10 files, ~+1027 |
| Uncommitted in worktree | Incremental (planner.py, stories/triage routers, Dockerfile, deploy/, 2 test files) | **The majority of the implementation** -- all of `routers/`, `models.py`, `repository.py`, `wire.py`, `db.py`, `audit.py`, Dockerfile, 5 of 6 API test files are **untracked**. One `git clean` from gone. |
| Installs | Yes | Yes |
| Tests | 176 collected; **18 pass / 158 ERROR** without a live Postgres | 67 collected; **17 pass / 50 ERROR** without a live Postgres |
| Missing-DB behavior | Hard `pytest.fail` + docker instructions | Hard `pytest.fail`, attempts auto-docker first |
| DB-test isolation | Mixed into `tests/`, distinguished by fixture | **Cleaner**: `tests/` offline vs `tests/pg/` DB-bound; `pytest --ignore=tests/pg` is a real green |
| Wire parity w/ frontend | **Confirmed** across ~8 route groups; card shape matches | Confirmed, equivalent |
| Import tooling | `scripts/import_legacy.py` (295 lines, dry-run, refuses non-empty orgs) | None |
| Wire-contract doc | **None** (ADR only) | **`docs/board/CARDS_API_CONTRACT.md`** (228 lines) |
| RLS bypass tests | 9 (ORM-mediated) | 13 (raw-engine) incl. **2 append-only grant tests A lacks** |
| Packaging bug | **Found and fixed** (`packages=["cards_api"]` silently drops `routers/` from non-editable wheels) | **Present and unfixed** -- a real wheel build contains **zero** files from `routers/`; its own Dockerfile does a non-editable `pip install .`, so **that image would fail to boot with ImportError** |

**Both fail-loud rather than skip when Postgres is absent, explicitly citing my audit's S1 finding** ("silently skipping the security suite is exactly the CI-masking failure the audit flagged"). Both use `FORCE ROW LEVEL SECURITY`, a `NOSUPERUSER`/`NOBYPASSRLS` app role, transaction-local `app.current_org`, fail-closed on NULL, and no `create_all()` in runtime paths.

**Is this rushed work? No -- and that matters for the "don't force-salvage" instruction.** Branch A independently found and fixed a real packaging bug (empirically confirmed by building actual wheels for both branches), expanded ruff to `E,F,B,I,UP,SIM`, gated mypy with `disallow_untyped_defs`, and designed its test suite to fail loudly against CI-masking. That is careful engineering, not a rushed stitch. **The problem with Branch A is not quality. It is brief.**

**Notable convergence:** the two sessions independently reached essentially the same architecture (Postgres 16, SQLAlchemy 2 sync, psycopg 3, Alembic, FORCE RLS, two-role owner/app split, transaction-local org context, fail-closed, app-layer WHERE retained as defense-in-depth, raw-SQL bypass tests). Two agents converging from the same audit is real corroboration that **the architecture is right**. That design survives regardless of what happens to the code.

**They do not merge.** The branches collide on 5 files -- `alembic.ini`, `migrations/env.py`, `script.py.mako`, `pyproject.toml`, and **both authored their own `0001_initial_schema_rls.py`**. `git merge-tree` shows 8 conflict signals. Merging them means hand-resolving a conflict on the RLS policy definition -- the security boundary itself. Ruled out.

### 1b. Track K/L/S/P -- "AgileCards: Fable takeover to MVP" (one session)
Source of truth: `C:\dev\AGILECARDS_MVP_LOCALGPU_ASSESSMENT_2026-07-14.md` (with addenda through 2026-07-15). This is the only K/L/S/P design doc on disk. Drew locked sequencing **L → S → P, sequential, Fable-level, no fan-out** (2026-07-15).

| Track | Scope (from the doc) | Verified state |
|---|---|---|
| **L** -- provider-agnostic execution | KL1 pricing/tier-map; KL2 provider adapter port + 3 adapters (anthropic / openai_compat / gemini) covering 5+ providers; KL3 tool-use across the port; KL4 per-card provider+size routing (extends the stakes×difficulty grid); KL5 eval + honesty gate (per-model card-completion rate vs the AC gate) | **KL1 DONE** (#51), **KL2 DONE** (#52, #53) -- `providers/` + both tier maps are on main. **KL3 in progress**: `kl3-tooluse-port`, 1 WIP commit, 385 lines, **openai-compat path only** (anthropic/gemini tool_turn remain). KL4/KL5 not started. |
| **S** -- sprint → orchestrator wire | "Make a UI-laid-out sprint drive what the orchestrator claims/prioritizes and report progress back into the sprint." Both ends real, the seam is missing: the engine has **zero sprint code**; the runner claims from the backlog folder by dependency-eligibility and never reads sprints (which live in the board's SQLite). | **NOT STARTED.** No branch exists. Verified: `grep -rli sprint engine/**/*.py` returns **nothing** -- the doc's gap claim is accurate. |
| **P** -- portal federation | Port the live portal-shell token contract into `cards_api/auth.py` (`iss`=portal origin, `aud`="gantry", `customer_id`/`role`, drop required `nbf`/`org_id`), retire `TokenGate.tsx` behind the wired `portalHandoff.ts`, nginx `/gantry/` overlay + brand. **P1 decided** 2026-07-15 (federate against the live portal contract, aligns with platform's locked DR-7). **P2 open** (internal tool vs customer-facing categorization -- needs Drew). | **NOT STARTED.** No branch exists. Has a coordination dependency on the paradigm-platform session. |

**Track L largely passes Drew's "still standing three phases out" test already.** The provider port was deliberately built neutral and extensible (`ToolSpec` = name + schema + executor, explicitly designed so a future non-code/fabrication tool type plugs in without rework). That is principle 2 correctly pre-applied. Track L is the healthiest thing in this repo.

---

## 2. Overlap analysis -- duplicate vs complementary

| My brief (PR #55) | K/L/S/P | Verdict |
|---|---|---|
| **Phase 1** -- real persistent agent-native backend | (not in K/L/S/P scope) | **Duplicate of the backend pair's work**, but *their* version is legacy-parity-only, not agent-native. See §3. |
| **Phase 2** -- deploy + monitoring | (not in scope) | **Unowned.** Branch A has an uncommitted `Dockerfile` + `deploy/`; nobody owns Phase 2 formally. |
| **Phase 3** -- unify execution paths + wire board to runner | **Track S** | **REDUNDANT. Track S owns this.** See §2a. |
| **F2** cost/tokens per card | **KL4** (per-card provider+size routing, cost-aware policy) | **Complementary but must share one data model.** KL4 owns the routing *decision*; F2 owns *surfacing* cost on the card. If KL4 invents an engine-side cost shape and F2 invents a board-side one, that's two models for one fact. |
| **F9/F11/F12** kaizen analytics, rework tracking, fleet DORA metrics | **KL5** (eval + honesty gate, per-model completion rate vs AC gate) | **Complementary, shared substrate.** KL5 produces the measurement; F9/F12 are the board-facing surface of it. Same risk as above: one substrate, or two. |
| **F6** pull dispatch + dependency eligibility | Engine `eligibility.py` (exists) + **Track S** | **Mostly exists.** Surfacing it is Track S's reporting direction. |
| **F1/F3/F4/F5/F7/F8/F10** -- AC as first-class card field + badges; agent/human attribution + lifecycle; worktree-aware cards; concurrency limits tied to cost+review capacity; gate latency metric; card-scope limits; supervision console (tier-3 approve, intervene) | *(nothing)* | **GENUINELY NEW AND UNOWNED.** This is the real, non-duplicative contribution of the vision brief. |
| *(nothing)* | **Track P** -- portal federation | **Unowned by me; K/L/S/P owns it.** My brief never mentioned it. Not a conflict, a gap in my coverage. |

### 2a. Phase 3 vs Track S -- the honest call
Track S is defined as: *sprint drives what the orchestrator claims/prioritizes* **and** *progress reports back into the sprint*. That is **both directions of the board↔runner seam**. My Phase 3 was: *route submit-story through the runner (kill the second `claude`-CLI execution path)* **and** *make the board read the runner's live state*.

**Phase 3 is substantially subsumed by Track S and should be dissolved into it.** Track S is the owner; it is the more precisely-scoped version; it has a locked sequencing decision from Drew; and building a parallel Phase 3 would mean two sessions building one seam. My Phase 3 contributes exactly two things Track S does not currently name, and they should be **folded into Track S's scope** rather than kept as a separate phase:
1. **Execution-path unification** -- the board's submit-story currently shells out to the `claude` CLI via legacy Express, a second execution path entirely disconnected from the runner daemon. Track S's doc does not mention retiring it. It must, or the seam is built while a competing path survives.
2. **The observability payload spec** -- Track S says "report progress back into the sprint" without specifying depth. F3/F4 (agent/human attribution, lifecycle state, worktree/branch, diff size, PR/CI status) is what "report progress" should actually carry. If Track S ships a thin progress field and F3/F4 arrive later, the seam's contract changes -- a principle-2 violation.

---

## 3. The two architectural fault lines (found by applying Drew's principle 2)

### FAULT LINE 1 -- the card store is split-brain, and one side has already picked a winner unilaterally

- **Engine model (documented, live):** "the board watches the card tree on disk and serves SSE; the engine writes cards to that tree. **Card files (`C:\dev\todo\`) are the source of truth.**" The runner claims cards from a filesystem backlog folder.
- **Backend model (Branch A, built):** cards are Postgres rows. The file path is **synthetic and derived**:
  ```python
  @property
  def file(self) -> str:
      """Synthetic stable file key preserving the legacy folder/name shape."""
      return f"{STATUS_FOLDERS[self.status]}/{self.id}.md"
  ```
  Plus `scripts/import_legacy.py`, a **one-way** file-tree → Postgres import.

So the backend has decided **Postgres is truth and the file is a derived artifact**, while the engine is simultaneously being extended (Track L, and next Track S) on the premise that **the file tree is truth**. Both are live. Neither session knows the other made the call. **This is not a merge conflict; it is a contradiction in the domain model**, and it was decided inside a backend branch without the engine session in the room.

**Why this is a fix-now under principle 2:** Track S is the wire between sprints (board/Postgres) and the orchestrator (filesystem). Build it now and it encodes whichever answer is implicit at the time; answer the question later and the wire is rewritten. Every feature interconnected on top of it -- KL4 routing, KL5 eval, F1-F12 -- compounds the cost. This is exactly "deferred architectural debt compounds into a forced rewrite."

**This is the #1 question for the council.** Options, none free:
- **(a) Postgres authoritative; the runner reads/writes the DB** (via the API or a repo layer). Clean single truth. Cost: the engine's clean process-boundary/no-coupling architecture (a genuine strength) gains a DB dependency; the runner can no longer operate on a plain file tree.
- **(b) Filesystem authoritative; Postgres is a projection/read-model** the board serves. Preserves the engine's independence and the "cards are files" property (which is also what makes worktree-per-agent and git-native workflow natural). Cost: the backend becomes a sync/projection problem (and RLS over a projection is doable but the write path gets subtle).
- **(c) Split by domain** -- customer/board cards in Postgres, operator/runner cards on disk, with an explicit, narrow, versioned contract between them. Honest about them being two different products (SaaS vs operator tool). Cost: two card models forever; the "one board for humans and agents" vision gets harder.
- **(d) Event-sourced middle** -- one append-only event log as truth, both the DB and the file tree as projections. Most architecturally satisfying, most expensive, and overkill for alpha.

I have a lean (see §5) but this is a genuine architecture fork with real trade-offs and it is exactly what a council is for.

### FAULT LINE 2 -- `0001` carries no agent-native model

Verified against Branch A's `models.py` and `0001_initial_schema_rls.py`. Hit counts for agent-native concepts:

| Concept | `models.py` | `0001` migration |
|---|---|---|
| agent | **0** | **0** |
| worktree | **0** | **0** |
| cost | **0** | **0** |
| attempt / rework | **0** | **0** |
| acceptance / AC | **0** | **0** |
| confidence | **0** | **0** |
| provider | **0** | **0** |

The `Card` model is `org_id, id, status, frontmatter(JSONB), body, created_at, updated_at` -- the legacy markdown card, faithfully relocated into Postgres. **This is correct for the brief it was given and insufficient for the vision.**

**How bad is it, honestly?** Less catastrophic than it first looks, and I will not inflate it:
- Some agent-native scalars *could* ride in `frontmatter` JSONB with no migration. But JSONB is exactly the bolt-it-on-later pattern: weak indexing/constraints, no FKs, and the analytics that F2/F9/F12 need (cost rollups, rework rates, gate latency) over JSONB is painful.
- The genuinely missing pieces are **relations, not columns**: an `agent_runs` / `card_attempts` / cost-ledger / gate-verdict table simply does not exist, and those are not fields you tuck into frontmatter.
- Adding tables in `0002` is a normal **additive** migration -- that *is* evolution, not rewrite, so this alone does not force a rebuild.

**The real principle-2 exposure is Fault Line 1, not Fault Line 2.** Missing tables are additive; a contradicted source-of-truth is not. But the two interact: you cannot sensibly design the agent-native card model until you know *which store owns a card*.

---

## 4. Ownership map -- one owner per item, going forward

| Work | Owner | Status |
|---|---|---|
| Track L (KL1-KL5): provider port, tool-use, per-card routing, eval | **K/L/S/P session** | KL1/KL2 done; KL3 in progress; KL4/KL5 pending |
| Track S: the board↔runner seam (**absorbs my Phase 3**, incl. execution-path unification + the observability payload) | **K/L/S/P session** | Not started -- **blocked on Fault Line 1** |
| Track P: portal federation | **K/L/S/P session** (+ platform coordination) | Not started; P2 open for Drew |
| Phase 1: real Postgres+RLS backend, **agent-native** | **ONE owner -- the new Fable session** (the duplicate pair stands down) | Architecture proven twice; schema needs re-cutting (§5) |
| Phase 2: deploy + monitoring | **New Fable session** | Unowned today; Branch A has uncommitted Dockerfile/deploy as raw material |
| Board-as-control-plane surface (F1, F3, F4, F5, F7, F8, F10) | **New Fable session** | The genuinely new scope |
| Audit must-fix/should-fix (S1 CI gates, Gantry purge, naming, etc.) | **New Fable session**, opportunistically | Branch A already did M1 (legacy relabel), M3 (smoke fix), and expanded lint/mypy (S12/S13) |
| Cost/measurement data model (F2 ↔ KL4; F9/F11/F12 ↔ KL5) | **Shared contract -- must be co-designed, single model** | The coordination hazard to watch |

---

## 5. Keep / discard / rebuild -- recommendation

Applying "done properly from the beginning" honestly, which cuts **both** ways: it argues against force-salvaging rushed work, and equally against gratuitously retyping correct work.

**Branch B -- DISCARD.** Its implementation is almost entirely uncommitted, it is strictly behind A, and it carries an unfixed packaging bug that would break its own Docker image on first boot. **Salvage exactly two things, both already safely committed:**
1. `docs/board/CARDS_API_CONTRACT.md` (228 lines) -- A has no wire-contract spec; this is genuinely additive.
2. Two tests from `tests/pg/test_rls_enforcement.py`: `test_audit_log_is_append_only_for_app_role` and `test_card_events_are_append_only_for_app_role` -- A never asserts the append-only grant at the DB layer. Real gap, real fix.
   *(B's other 11 RLS tests duplicate A's coverage. B's raw-engine test style is arguably cleaner than A's ORM-mediated style -- a stylistic note, not a gap.)*

**Branch A -- KEEP AS PATTERN AND CODE SOURCE; RE-CUT `0001`.** Not "adopt wholesale," not "throw away":
- **Reuse** (it is correct and expensively learned): the RLS mechanics (FORCE + two-role split + transaction-local org + fail-closed), the audit-grant model, the wire/serialization layer, the fail-loud test design, the packaging fix, the expanded ruff/mypy config, and `import_legacy.py`.
- **Re-author** `0001` so the foundational migration carries (i) the source-of-truth semantics the council decides, and (ii) the agent-native model from the start. `0001` is the foundation; per principle 2, a foundation designed for a superseded brief gets fixed at discovery, not migrated over later.
- **Do not** stitch A and B together. They collide on `0001` and `pyproject.toml`; a hand-merge on the RLS policy is the exact rushed-stitch-on-the-security-boundary outcome to avoid.

**My lean on Fault Line 1 (for the council to test, not to rubber-stamp):** option **(b) or (c)** over (a). The engine's file-native card model is not an accident -- it is what makes worktree-per-agent, git-native diffing, and a zero-coupling operator tool work, and it is the most mature, best-tested thing Drew owns. Making the runner depend on Postgres to claim a card trades a proven strength for schema convenience. But (b) puts real weight on the projection/sync path, and (c) admits two card models forever, which cuts against the one-board vision. I hold this loosely; it is the council's call.

---

## 6. What I could not verify

- **Runtime RLS behavior on either branch.** No live Postgres/Docker in this environment; all DB-bound tests error at fixture setup by design. Their correctness beyond "the fixture correctly detects no DB" is unexercised here.
- **Whether Branch B's uncommitted worktree content was staged for a commit that never landed**, or was abandoned. Git can't answer that; it's a question for that session's driver.
- **Cross-session intent.** Cross-session transcript reads are blocked from this session, so everything about the other sessions' reasoning is inferred from their commits, ADRs, and the K/L/S/P design doc -- all of which are, fortunately, unusually well documented.
- **Whether the K/L/S/P session considers Track S's scope to include the two additions in §2a.** Its doc does not mention them; I could not ask.

---

## 7. Immediate, actionable flags

1. **Two live sessions are building on contradictory card models** (Fault Line 1). Until resolved, Track S should not start, and no more should be built on `0001`.
2. **Branch B's worktree holds ~15 files of uncommitted implementation** -- one `git clean` from gone. Since the recommendation is to discard B (salvaging only committed assets), this is acceptable risk, but Drew should know before anyone tidies worktrees.
3. **My audit's Decision-1 recommendation (Option A, ship legacy) is superseded** by Drew's confirmed ruling. PR #54 and PR #55 both need that corrected so the record doesn't show a dead recommendation as live.
4. **The duplicate pair should stand down** on backend work so Phase 1 has exactly one owner.

---

# CORRECTION NOTICE -- appended 2026-07-16, after the council

**This memo's two headline findings (Fault Line 1 and Fault Line 2) are WRONG. They are preserved above unedited, because the record of how a confident error survived to the highest-stakes call in the package is itself the finding.** The corrections below are verified against `origin/main` @ `7c9ce99`. Where this notice conflicts with anything above, **this notice wins**.

## FL1 (the split-brain card store) does not exist

**Claimed above:** the engine treats card files on disk as the source of truth, the new Postgres backend made the file path a synthetic derived property, and Track S is a wire between two contradictory models.

**Verified fact:** the engine has been **database-canonical since the chunk 2b cutover**. `daemon.py` header: *"The daemon main loop, store-backed... Per the cards-are-state principle the daemon holds NO durable card state of its own. **The store is the single source of truth**... v1's claim was an atomic file move (`backlog/` -> `active/`)... **The claim is now a transactional conditional `UPDATE` in the card store**."* `_try_claim` calls `self.repo.claim_card(...)`. `store/README.md`: *"Model B, **database-canonical with the card file preserved as a per-run projection**."* Both sides already agree. There is no contradiction and there never was.

**How the error happened, stated plainly:** the premise was sourced from `AGILECARDS_MVP_LOCALGPU_ASSESSMENT_2026-07-14.md`, a **status doc**, which asserts "Card files (`C:\dev\todo\`) are the source of truth." That doc is stale against `main`. The audit's own method section says status docs are untrusted and must be re-verified against code. **This memo applied that rule to everyone else's claims and not to its own inputs.** The Contrarian caught it in Round 1 by grepping the store package, and named a ten-minute check as its flip condition. The check ran. It was right.

**Consequence:** FL1's options (a)/(b)/(c)/(d) are moot as framed. The User-proxy's Round 1 vote for (b) rested on the same stale premise and was withdrawn.

## FL2 (`0001` carries no agent-native model) is true of the backend and false of the product

**Verified fact:** `engine/runner/src/cards_runner/store/schema.py` `CARD_COLUMNS` already carries `tenant_id` (first PK column on every table), `claimed_by`, `attempt_trace_id`, `model_used`, `last_heartbeat`, `merge_status`, `verified_at`, `verified_by`, `estimated_tokens`, `actual_tokens`, `story_hash`, `trace_id`, `pr_url`, `work_type`, `stakes`, `difficulty`. `EXPECTED_TABLES` also includes `card_events` (with `actor_id` + `actor_type`), `dependencies`, `card_metrics`, `metric_estimates`, `gate_ramp`.

**That is the agent-native model.** It maps onto nearly every feature this package proposed as "genuinely new and unowned": attribution (F3), cost per card (F2), gate verdict (F1), liveness (F3), attempts (F11), PR link (F4), and the measurement substrate (F7/F9/F12). Branch A did not fail to invent an agent-native schema. It failed to look at the one shipped thirty files away. So did Branch B. So did this memo.

## What replaced them (the council's actual findings)

1. **`store/README.md` already specs the Postgres path** ("documented, not built"): `PostgresRepository(_SqlCardRepository)`, `SELECT ... FOR UPDATE SKIP LOCKED`, RLS keyed on `tenant_id`. **But the engine has ZERO RLS today** -- `grep -rniE "row level|force row|current_setting|create policy" engine/runner/src` returns **zero hits**, and the default store is SQLite, which cannot have RLS. That README line is a **promise about unbuilt code**. This memo quoted it as if it were shipped. It is not.

2. **The seam does not fit the board.** Engine `EXPECTED_TABLES` (8): `cards, card_events, batches, dependencies, counters, card_metrics, metric_estimates, gate_ramp`. Branch A `0001` (10): `cards, card_rank, card_events, saved_views, sprints, sprint_cards, retros, story_batches, staged_cards, audit_events`. **Overlap: 2.** The engine has zero hits for sprints, retros, saved_views, ranks, triage, or audit_events. So "put the board behind `CardRepository`" means either the runner's port grows PM tables the daemon will never read, or two stores plus a seam. The Contrarian proposed this idea in Round 1 and killed it in Round 2 on its own evidence.

3. **FL-D was never open.** `portal-gameplan-opus` **DR-9**, a Drew ruling dated **2026-07-08**: *"Agile Cards STAYS Vite+Python blessed internal dev-tool NOT a customer SKU"*, `listedInCatalog=false`. Corroborated by `project_agilecards_agentic_vision` (2026-07-16): the pivot is *"a dogfooding argument, **not a hypothetical market claim**."* **The market framing in `02_REVISED_HANDOFF.md` was disclaimed by Drew's own vision doc eight days before the council convened.** With no tenant, Postgres/`org_id`/RLS/tenancy leave Phase 1 entirely, and the FL-C argument becomes moot because the work does not happen.

4. **THE FINDING THAT OUTRANKS EVERYTHING IN THIS MEMO:** the engine's default store is `sqlite:<todo-root>/cards.db` (todo-root = `C:\dev\todo`). **`C:\dev\todo\cards.db` does not exist. The card tree holds zero cards.** No AgileCards store DB was found under `C:\dev`. The `LedgerWriter` is correctly wired (7 call sites in `daemon.py`), so `card_metrics` *would* populate. **It never has.** The engine has 713 passing tests and has apparently never been run against a real backlog on this machine. *(Caveat: `CARDS_STORE`/`CARDS_TODO_ROOT` could redirect the store, the scan timed out, and Drew has a second machine.)*

## What survives from this memo unchanged

The verified branch facts in section 1a (Branch A vs Branch B: sizes, test counts, the packaging bug found-and-fixed in A and unfixed in B, B's uncommitted majority, the 5-file collision and the duelling `0001` migrations), the Track K/L/S/P state in 1b (KL1/KL2 merged, KL3 one WIP commit, S and P unstarted with no branches, engine has zero sprint code), and the ownership map's core point: **one owner per item**. The recommendation in section 5 ("keep Branch A as pattern source, re-cut `0001`") is superseded -- see `06_COUNCIL_SYNTHESIS.md`.

## The lesson worth keeping

The council cost real tokens and its single highest-value output was destroying the confident framing of the document that convened it. Two personas independently caught the error in Round 1 by doing the one thing the memo's author did not: reading the store package instead of the doc that described it. **The process that produced FL1 -- trusting a status doc on the highest-stakes call while instructing everyone else not to -- is the process that produced this memo.** Whatever gets built next, verify against `main` before deciding, not after.
