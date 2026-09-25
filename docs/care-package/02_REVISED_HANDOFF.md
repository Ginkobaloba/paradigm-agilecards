> # SUPERSEDED NOTICE -- read before anything below
>
> **Appended 2026-07-16, after the council.** This document's §2 (Fault Line 1, the "split-brain card store") and §3 (Fault Line 2, "`0001` carries no agent-native model") are **verified FALSE**, and its market framing was **disclaimed by Drew's own vision doc eight days before the council convened**. It is preserved unedited because the record matters, but **do not act on it**.
>
> - **FL1 is not real.** The engine has been database-canonical since the chunk 2b cutover. `daemon.py`: *"The store is the single source of truth."* `_try_claim` calls `repo.claim_card(...)`, not a file move. `store/README.md`: *"Model B, database-canonical with the card file preserved as a per-run projection."* The premise in §2 came from a **status doc** (`AGILECARDS_MVP_LOCALGPU_ASSESSMENT_2026-07-14.md`), which the audit's own method says to distrust.
> - **FL2 is false of the product.** `store/schema.py` already carries the agent-native model (`claimed_by`, `model_used`, `estimated_tokens`, `actual_tokens`, `merge_status`, `verified_at`, `attempt_trace_id`, `pr_url`, `stakes`, `difficulty`, `tenant_id`), plus `card_events`, `card_metrics`, `metric_estimates`, `gate_ramp`. It is missing from the *backend's* `0001`, not from the repo.
> - **The market framing in §1 is dead.** `portal-gameplan-opus` DR-9 (Drew ruling, 2026-07-08) already made AgileCards an internal dev-tool, `listedInCatalog=false`. Competitors are irrelevant to a tool you build for yourself.
> - **The §6 sequencing (Phases 0-4) is vetoed** as calendar cosplay. The unit is PRs against tiered review capacity.
>
> **What survives:** §5 (Track L passes the binding test), the ownership map's core point (one owner per item), and the two additions Track S must absorb. **Everything else: see `06_COUNCIL_SYNTHESIS.md` and `07_FABLE_BRIEF.md`.**
>
> **The finding that outranks this entire document:** the engine has 713 passing tests and has never been run against a real backlog. `card_metrics` has zero rows.

# REVISED HANDOFF -- AgileCards as a Must-Have Tool for Agentic Software Development

**Date:** 2026-07-16 (revision 2)
**Supersedes:** `docs/handoffs/HANDOFF_2026-07-16_agentic-dev-vision-and-audit-synthesis.md` (PR #55) -- that version did not know the K/L/S/P track existed. Where they conflict, **this wins**.
**Companions:** `01_RECONCILIATION_MEMO.md` (who owns what -- read first), `docs/audits/AUDIT_2026-07-16_alpha-gap-list.md` (PR #54, the verified state), `03_AGILE_AGENTIC_RESEARCH.md` (the grounding).
**Baseline:** `origin/main` @ `7c9ce99`.

---

## 0. What changed in this revision, and why

The first version of this handoff was written without knowledge of a third active session running a **K/L/S/P track roadmap** (`C:\dev\AGILECARDS_MVP_LOCALGPU_ASSESSMENT_2026-07-14.md`). Reconciliation (memo `01`) established four corrections. Stating them plainly rather than quietly editing:

1. **Phase 3 is dead as a separate phase.** "Unify the two execution paths + wire the board to the runner" is **Track S**, which already owns it, is more precisely scoped, and has a locked sequencing decision from Drew. My Phase 3 is dissolved into Track S, contributing two additions to Track S's scope (below). Building it as a parallel phase would mean two sessions building one seam.
2. **My audit's Decision-1 recommendation is superseded and should stop being treated as live.** I recommended Option A (ship legacy Express now, build FastAPI as follow-through). Drew ruled the opposite: **build the real backend, do not ship alpha on legacy Express.** Confirmed by Drew directly and independently recorded in both backend branches' ADRs. The audit doc (PR #54) still shows my recommendation; it is dead.
3. **Phase 1 is already substantially built -- twice -- but to the wrong brief.** Two duplicate sessions independently built Postgres+RLS backends. The work is careful, not rushed, and the two converged on the same architecture (strong corroboration the design is right). But both were briefed on the audit's narrow P1 (legacy wire parity) and neither carries the agent-native model. See §3.
4. **Two live sessions are building on contradictory models of what a card is.** This is the most consequential finding in the whole reconciliation and it outranks everything else in this document. See §2.

---

## 1. The thesis (unchanged, and reconciliation strengthens it)

**Do not build a better kanban. Build the control plane and observability surface for the multi-agent execution engine AgileCards already owns.**

Nothing in the reconciliation weakened this. It sharpened it: the K/L/S/P track is *itself* evidence for the thesis. Track L is making card execution provider-agnostic and per-card cost-routed; Track S is trying to make a sprint drive an orchestrator. Those are agent-native project-management features being built because the work demands them. The vision is not a pivot away from what the other session is doing -- **it is the coherent name for what it is already doing piecemeal**, plus the observability/control surface nobody owns yet.

What the reconciliation *did* change is my claim to scope. Most of what I called "powerful features" is either already owned (Track L/S/P) or already built. The genuinely new, unowned contribution is narrower and more honest: **F1, F3, F4, F5, F7, F8, F10** -- acceptance criteria as a first-class card field with gate badges, agent/human attribution + lifecycle state, worktree-aware cards, concurrency limits tied to cost and review capacity, gate-latency as a flow metric, direct card-scope limits, and the human supervision console. That is the board-as-control-plane surface. It is real, it is unowned, and it is the thing that makes the product must-have.

---

## 2. THE BLOCKER: the card store is split-brain (Fault Line 1)

Applying Drew's rule -- *"will this design still be standing, unmodified, three phases from now?"* -- surfaced a contradiction that blocks the whole plan.

- **Engine model (documented, live, mature):** card files on disk (`C:\dev\todo\`) **are the source of truth**. The board watches the tree and serves SSE; the engine writes to it. The runner claims cards from a filesystem backlog folder by dependency-eligibility. This zero-coupling process boundary is a genuine architectural strength.
- **Backend model (Branch A, built):** cards are **Postgres rows**; the file path is a *synthetic, derived property* (`f"{STATUS_FOLDERS[self.status]}/{self.id}.md"`), plus a one-way file→Postgres import script.

**Both are live. Neither session knows the other made the call.** The backend unilaterally resolved the question inside a feature branch without the engine session in the room.

**Track S is exactly where they collide.** Track S wires sprints (board-side, Postgres after Phase 1) to the orchestrator (filesystem). Build that wire before deciding which store is authoritative and it encodes whichever answer is accidental at the time -- then gets rewritten when the question is actually answered, with KL4 routing, KL5 eval, and F1-F12 all interconnected on top of it by then. That is precisely the deferred-debt-compounds-into-forced-rewrite pattern Drew's principle 2 forbids.

**Therefore: Track S does not start, and nothing further is built on `0001`, until this is decided.** The options (a) Postgres-authoritative, (b) filesystem-authoritative with Postgres as projection, (c) split by domain with a narrow versioned contract, (d) event-sourced with both as projections -- are laid out with trade-offs in memo `01` §3. This is the council's first question.

---

## 3. Phase 1's real state: right architecture, wrong brief (Fault Line 2)

Both backend branches are careful work. Branch A independently found and fixed a real packaging bug (verified by building actual wheels), designed its tests to **fail loudly rather than skip** when Postgres is absent -- explicitly citing my audit's S1 CI-masking finding -- expanded ruff to `E,F,B,I,UP,SIM`, gated mypy, used `FORCE ROW LEVEL SECURITY` with a `NOSUPERUSER`/`NOBYPASSRLS` app role and transaction-local org context that fails closed. That is not rushed; that is good engineering.

**But its `0001` migration carries zero agent-native model.** Verified hit counts across `models.py` and `0001_initial_schema_rls.py`: `agent` **0**, `worktree` **0**, `cost` **0**, `attempt`/`rework` **0**, `acceptance`/`AC` **0**, `confidence` **0**, `provider` **0**. The `Card` is `org_id, id, status, frontmatter(JSONB), body, created_at, updated_at` -- the legacy markdown card faithfully relocated into Postgres.

**Honest severity:** less than it first appears. Some agent-native scalars could ride in `frontmatter` JSONB; and adding an `agent_runs` / `card_attempts` / cost-ledger table in `0002` is a normal **additive** migration, which *is* evolution, not rewrite. So Fault Line 2 alone does not force a rebuild. The real exposure is Fault Line 1 -- **a contradicted source-of-truth is not additively fixable, and you cannot sensibly design the agent-native card model until you know which store owns a card.**

**Recommendation (memo `01` §5):** keep Branch A as pattern-and-code source -- reuse the RLS mechanics, audit grants, wire layer, fail-loud tests, packaging fix, lint/mypy config, and `import_legacy.py`. **Re-author `0001`** so the foundational migration carries the council's source-of-truth decision and the agent-native model from the start. Discard Branch B, salvaging exactly two committed assets: `docs/board/CARDS_API_CONTRACT.md` (A has no wire-contract spec) and its two DB-layer append-only grant tests (a real gap in A). Do **not** stitch A and B -- they collide on `0001` and a hand-merge lands on the RLS policy itself.

---

## 4. Ownership -- one owner per item (the anti-duplication contract)

| Work | Owner |
|---|---|
| Track L (KL1-KL5): provider port, tool-use, per-card routing, eval/honesty gate | **K/L/S/P session** (KL1/KL2 done, KL3 in progress) |
| **Track S**: the board↔runner seam -- **absorbs my Phase 3** | **K/L/S/P session** -- blocked on Fault Line 1 |
| Track P: portal federation | **K/L/S/P session** (+ platform coordination); P2 open for Drew |
| Phase 1: real Postgres+RLS **agent-native** backend | **New Fable session** -- the duplicate pair stands down |
| Phase 2: deploy + monitoring | **New Fable session** (Branch A has uncommitted Dockerfile/deploy as raw material) |
| Board-as-control-plane surface (F1/F3/F4/F5/F7/F8/F10) | **New Fable session** -- the genuinely new scope |
| Audit must-fix/should-fix (S1 CI gates, Gantry purge, naming) | **New Fable session**, opportunistically (Branch A already did M1, M3, S12/S13) |
| **Cost + measurement data model** (F2↔KL4, F9/F11/F12↔KL5) | **Shared contract, co-designed, ONE model** -- the top coordination hazard |

### Two additions Track S must absorb (they are not in its doc today)
1. **Execution-path unification.** The board's submit-story shells out to the `claude` CLI via legacy Express -- a second execution path entirely disconnected from the runner daemon. Track S's design doc never mentions retiring it. If Track S builds the seam while a competing execution path survives, the unification never happens.
2. **The observability payload spec.** Track S says "report progress back into the sprint" without specifying depth. F3/F4 define what "progress" must actually carry: agent/human attribution, lifecycle state, worktree/branch, diff size, PR/CI status. If Track S ships a thin progress field and the rich payload arrives later, the seam's contract changes -- a principle-2 violation on the very seam we just protected.

---

## 5. Where Track L already gets it right (worth copying, not just noting)

Track L largely **passes** Drew's three-phases-out test already. The provider port was deliberately built neutral and extensible -- `ToolSpec` = name + schema + executor, explicitly designed so a future non-code/fabrication tool type plugs into the same port/loop/invoker without rework. That is principle 2 correctly pre-applied, by the session that had the least reason to think about it. It is the healthiest architecture in the repo and the standard the rest of this work should be held to.

The one honest caveat Track L already documents: small local models are much weaker at reliable multi-turn tool-calling than at plain reasoning, so KL3's real value is capped, and KL5 exists specifically to measure it rather than assume it. That intellectual honesty is the standard too.

---

## 6. Revised sequencing

Phases are re-numbered because Phase 3 is gone and the fault lines re-order everything.

- **Phase 0 -- Resolve the fault lines (COUNCIL, then Drew).** Fault Line 1 (source of truth) and the agent-native card model. **Nothing else starts.** Also: P2 (internal vs customer-facing) and whether the vision is committed direction.
- **Phase 1 -- Real, persistent, agent-native backend.** Re-cut `0001` carrying the decided source-of-truth semantics + the agent-native model. Reuse Branch A's proven mechanics. Salvage B's two assets. One owner.
- **Phase 2 -- Deploy + monitoring.** Dockerfiles for the real stack, one canonical URL, deploy job, structured logging, `paradigm-status/v1` report.
- **Phase 3 (was 4) -- The board-as-control-plane surface.** F1, F3, F4, F5, F7, F8, F10. The unowned, differentiating scope.
- **Phase 4 (was 5) -- Intelligence layer.** F8 scope limits + auto-decompose, F9 kaizen analytics, F11 rework/gaming tracking, F12 fleet DORA metrics -- **co-designed with KL4/KL5, one data model, not two.**
- **In parallel, owned by K/L/S/P:** KL3 → KL4 → KL5, then **Track S** (once Fault Line 1 is resolved), then Track P.

**Note on a real tension:** Drew locked "L → S → P, sequential, Fable-level agent, no fan-out" on 2026-07-15, and has now asked that mechanical subtasks be delegated to lighter models for token efficiency. These reconcile cleanly if read as: **the tracks stay sequential; delegation happens *within* a track**, on mechanical subtasks, not by forking the track order. That is the reading the Fable brief encodes.

---

## 7. Everything else from revision 1 that still stands

The audit's four tiers (`docs/audits/AUDIT_2026-07-16_alpha-gap-list.md`), the 12 agent-native features with their Agile-mechanism grounding (`03_AGILE_AGENTIC_RESEARCH.md`), the "where Agile does NOT map" honesty section, and the compliance cheap-seams posture are all unchanged and still correct. The only structural edits are the four in §0 plus the re-sequencing in §6.

---

## Next Session Onboarding

Read `C:\dev\SESSION_PROTOCOL.md`, this project's `CLAUDE.md`, then `01_RECONCILIATION_MEMO.md`, then this file, then the audit. If you are the Fable session commissioned to build, your complete brief is `07_FABLE_BRIEF.md`.
