# BRIEF — Build AgileCards into a Must-Have Tool for Agentic Software Development

**For:** a fresh, max-effort Fable session.
**From:** Opus 4.8 (audit + synthesis), commissioned by Drew Mattick.
**Status:** this brief SUPERSEDES the earlier narrower "Build real AgileCards backend" brief. This is self-contained — you can execute from this file alone. For depth, read the two companion docs (pointers at the end); you should skim both before mass implementation, but you do not need them to understand the mission.

---

## 1. Mission (read this first, twice)

AgileCards is Drew's flagship product ("our masterpiece"). Your job is to evolve it into **the must-have project tool for agentic software development** — teams and individuals who orchestrate multiple AI coding agents in parallel, exactly the way Drew's own operation runs (this very multi-agent Dispatch workflow, worktree-per-agent discipline, retro cadence applied to orchestration).

**The strategic thesis, which you must internalize and not drift from:** do NOT build a better human kanban. AgileCards already owns the hard, expensive asset — a real multi-agent *execution engine* (`engine/runner/`: worktree-per-agent lifecycle, dependency/merge-aware eligibility, a verifier cascade, a cost governor, tier-aware merge gating; 713 passing tests, the strongest code in the repo). The board, however, is a generic human kanban wired to a legacy backend and is **blind to that engine**. Every mainstream PM tool (Jira, Linear, Trello) models a human doing the work. None models an AI agent as a first-class actor with a worktree, a token budget, a machine-checkable stop condition, and non-deterministic output. **Your mission is to make the board the control plane and observability surface for the execution engine AgileCards already has.** That is the moat and the unoccupied category.

---

## 2. Ground truth you must know (verified against `origin/main` @ 7c9ce99)

- **Repo:** `paradigm-agilecards` is the live monorepo (`C:\dev\paradigm-agilecards`). `agile-cards` on disk is empty; `agile-cards-board` is the superseded pre-monorepo repo.
- **The board works, but on the wrong backend.** The polished board UI (kanban, triage, saved views, command palette, manual rank, card-event timeline, sprint planner + capacity, 2D grid, backlog grooming) is real and wired **entirely to `legacy/board-express/`** — the Express/TS backend the repo marks "delete after K11." Do NOT delete it until its replacement reaches parity; deleting it removes the running product.
- **The "real" backend is empty.** `backend/cards_api/` (FastAPI) is a ~490-LOC auth-guarded skeleton over an **in-memory dict** (`store.py:31`) with an incompatible card shape. It cannot serve the board. K11 (#47) shipped the JWKS auth + org-isolation *contract* only, not CRUD or the frontend cutover.
- **The engine is the moat** (`engine/runner/`). It already implements: worktree claim/heartbeat/orphan/reaper (`daemon/`), dependency+merge+drift+pre-approval eligibility (`daemon/eligibility.py`), a deterministic+subjective verifier cascade with confidence-based tier climb (`verifier/`, `worker_stub/sdk_invoker.py`), a cost governor + reviewer-cost accounting, calibration, and a tier-aware merge gate + shadow confidence gate. **The board and this engine only talk through the filesystem, and the board's submit-story path is a SECOND execution path that shells out to the `claude` CLI via Express.** Unifying these is central to the mission.
- **No deploy artifact exists for the current stack.** Every Dockerfile/compose targets the legacy Express app for the old host. No deploy job in CI. Deploy URL contradicts itself across three docs.
- **"CI green" overstates protection:** the live board tests run `continue-on-error`; the security-critical FastAPI auth suite is not a required check. Fix these early.
- **Strengths (don't regress them):** 1,100 tests pass repo-wide; everything compiles; strict TS with zero `any`; secrets are externalized via Infisical with none committed; the auth/isolation code is high quality.

---

## 3. The build — phased, foundation first

The agent-native features are worthless on a backend that can't persist or deploy. Build the floor before the roof. Each phase has acceptance criteria (AC); follow the repo's verification-driven pattern — a phase is done only when its AC have green verification records under `verification/<area>/<AC-ID>.md`.

### Phase 0 — Decisions (get Drew's answers before Phase 1 mass-build)
Four open decisions gate scope (full options + reasoning in the handoff §8):
1. Which backend does alpha ship on while Phase 1 is built? (recommendation: legacy Express now, as a stopgap, with Phase 1 as the committed replacement; relabel legacy "active until Phase 1", not "delete").
2. Is the marketing landing in alpha scope?
3. Does alpha claim local-GPU execution?
4. Is this agentic-dev vision the committed direction, or still a bet to pressure-test (a `/council` candidate before Phase 3+)? Phases 1-2 are needed regardless.
**AC:** Drew has answered 1-4; scope for Phase 1 is confirmed in writing.

### Phase 1 — Real, persistent, AGENT-NATIVE backend
Postgres + full card CRUD (reach parity with the ~20 routes the frontend needs: cards/columns/ranks/rates/sprints/stories/triage/views/events + SSE) + Postgres RLS for org isolation.
**The non-negotiable twist that separates this from the superseded brief:** design the schema **agent-native from day one**. A card row carries, as first-class columns/relations (not bolted on later):
- owner: agent instance + model + tier, OR human (attribution is first-class, not a text "assignee")
- lifecycle state: queued → eligible → claimed → executing → verifying → in-review → merged → failed/abandoned
- worktree + branch reference, diff size, PR link, CI status
- token + cost accrual (input/output/cache), escalation count
- structured, machine-checkable acceptance criteria (the stop condition) + gate verdict
- attempt/rework count (agents are non-deterministic)
Retrofitting these later is far more expensive than baking them in now.
**AC:** cards persist across restart; RLS enforces org isolation at the DB (verify with a cross-org leak test that fails closed); the agent-native columns exist and are populated by the runner; the FastAPI backend reaches CRUD parity such that the existing frontend can be pointed at it; the auth/isolation suite is a REQUIRED CI check (fix the `continue-on-error` gates as part of this).

### Phase 2 — Deploy + monitoring
Dockerfiles for the real FastAPI + Vite stack (+ the Node BFF the roadmap's K11b calls for if you host the TS llm-client server-side), one canonical deploy URL, a deploy job in CI, structured application logging, and a committed `paradigm-status/v1` report so the app shows up in `paradigm-ops` (which ingests `docs/status/*.md` via the GitHub API).
**AC:** `docker compose config` lints clean; the stack builds and starts from the artifact; one canonical URL in README + smoke config, with smoke assertions matching the real backend's shapes; a status report renders in paradigm-ops.

### Phase 3 — Unify execution + wire the board to the runner
Route the board's submit-story through the engine runner (delete the second `claude`-CLI path). Make the board read the runner's live state. This is where the board stops being a generic kanban.
**AC:** one execution path (the runner); the board shows live agent ownership + lifecycle state + worktree/branch per executing card; the existing `DependencyView` is wired to the runner's real eligibility DAG; a card cannot show "done" unless its gate is verified.

### Phase 4 — The agent-native control surface
Acceptance-criteria badges + gate verdict on cards (F1); cost/tokens per card with sprint/agent rollups (F2); concurrency/WIP limits tied to token budget + review-queue depth, not "focus" (F5); verify-gate latency as a flow metric (F7); the supervision console — tier-3 merge approval, agent-diff review, and kill/re-queue/escalate controls for stuck or looping cards (F10).
**AC:** each is demoable end-to-end against a real multi-agent run; tier-3 approval is a board action; a human can intervene on a stalled agent from the board.

### Phase 5 — Intelligence layer (the most differentiating)
Direct card-scope limits + auto-decompose oversized cards via the existing `/feature-decomposition` skill (F8); kaizen analytics — pass-rate/escalation/cost/rework sliced by card-type/prompt/tier, pointing at which orchestration config to change (F9); rework/specification-gaming tracking (F11); a fleet DORA dashboard — lead time, throughput, change-fail, MTTR per run/sprint/agent (F12).
**AC:** the retro view identifies a real config-improvement target from actual run data; the fleet dashboard renders real metrics.

---

## 4. The feature set and WHY each is right (grounded, not analogy)

Every feature maps to a real mechanism behind why Agile works, with an honest label. Full derivation + citations in the handoff §5. Condensed:

- **F1 Machine-checkable AC = the card's stop condition** — Toyota *jidoka* (build quality in; stop the line on defect). STRONGEST map: for an agent the DoD is the literal halt/reward signal, and a strict gate makes cheap/uneven agents safe by preventing merged garbage. **Caveat: the same LLM can game its own test — AC must be adversarially robust; taste-based "done" still needs a human.**
- **F2 Cost/tokens per card** — NEW constraint. Tokens are the first directly-measurable, per-item, real-money "effort." Replaces relative story points with measured spend.
- **F3 Agent/human attribution + lifecycle state** — Cockburn's information radiators. STRONGER for the human overseer (no peripheral awareness of 8 parallel agents otherwise); DOESN'T-MAP agent-to-agent (no osmosis — plumb inter-agent context explicitly).
- **F4 Worktree-aware cards** — DORA/trunk-based. STRONGER, load-bearing: the worktree is the unit of isolation; long-lived divergent branches are a merge-storm at machine speed.
- **F5 Concurrency limits as cost + conflict + review control** — Little's Law. SAME MATH, INVERTED BOTTLENECK: cap agents for token cost, merge-conflict surface, and the human reviewer's serial capacity — not focus.
- **F6 Pull dispatch on VERIFIED completion + eligibility** — Toyota pull/JIT. SAME/STRONGER and literal: dispatch on slot-free, treat "done" as gate-verified not agent-returned.
- **F7 Verify-gate latency as a flow metric** — fast-feedback economics. STRONGER (fully automatable), and the binding requirement becomes signal *quality* — flag flaky gates.
- **F8 Direct card-scope limits** — batch-size economics. STRONGER but INVERTS the proxy: an LLM can ship a huge change as one card, so constrain scope directly; don't infer batch size from cadence.
- **F9 Kaizen/orchestration analytics** — Toyota kaizen. STRONGER (improve prompts/gates/decomposition, versioned in git); the human psychological-safety-retro framing DOESN'T-MAP.
- **F10 Supervision console** — jidoka andon + Brooks's Law relocating onto the human orchestrator (the O(N) bottleneck). Make the reviewer's work cheap; that's the real throughput constraint.
- **F11 Rework/non-determinism tracking** — NEW constraint (no Agile precedent).
- **F12 Fleet DORA metrics** — Accelerate's four keys applied to the agent fleet (with F8's batch-size-proxy caveat).

---

## 5. What NOT to build (Drew asked for this honesty explicitly)

These are human-team artifacts that don't transfer or that invert. Building them is how "agentic agile" goes wrong:
- **Do not make fixed sprint cadence the core loop.** Timeboxes are a human coordination scaffold; agents run continuous one-piece flow. Keep sync/integration points, trigger them by human-review + merge windows, not a two-week clock. The existing sprint planner stays as an optional human-facing overlay, not the engine's heartbeat.
- **Do not build "team retros" with the agents or any psychological-safety/morale apparatus.** Agents have no fear or ego. Build kaizen *analytics* (F9), where the human edits the system.
- **Do not rely on agents "seeing" the board or overhearing each other.** No osmosis. Plumb inter-agent dependencies through explicit shared artifacts.
- **Do not model "sustainable pace" as agent wellbeing.** The real constraint is a token/compute budget + rate limits (a cost ceiling).
- **Do not port story points as relative effort.** Measured token cost (F2) is strictly better; keep points only as an optional human-planning overlay.
- **Do not build certification machinery** (SSP, POA&M, compliance dashboards) — out of scope. The compliance posture is "cheap seams only" (see constraints).
- **Do not anthropomorphize.** "The agents reflecting / self-organizing / owning" is a category error that will mislead the design.

The single test for any feature: **does it make the binding bottleneck — the human reviewer + token budget + merge integration — cheaper?** If not, it's a generic-kanban feature and probably not your job.

---

## 6. Hard constraints (non-negotiable)

- **Engineering bar:** SOLID, clean architecture, ACID where applicable, production-quality. This repo is also a portfolio artifact — senior-level or don't ship it. The engine/runner is the quality bar to match.
- **Git discipline (Drew-wide, hard rule):** every index-touching git command runs from **Windows PowerShell**, never a Linux/WSL sandbox against `C:\dev\` (it corrupts `.git/index.lock`). Never push to `main` directly; never `--force`; never `--legacy-peer-deps`; never change repo visibility; never bypass the verify gate. `delete_branch_on_merge` stays ON; stacked PRs merge bottom-up.
- **Worktree isolation:** one worktree per parallel agent — this repo has hit HEAD corruption from shared checkouts. (You are also building the productized version of this discipline; live it.)
- **Tier gates:** Tier-3 chunks (anything touching auth, the compliance seams, or the merge/deploy path) are **Drew-gated** — do not self-merge; open a PR and stop. Agent self-merge is future-only.
- **Compliance = cheap seams only:** keep/extend the seams the audit found (externalized secrets, FIPS-capable crypto) and add the cheap absent ones as you touch the relevant code (audit-log hook, TLS/HSTS on the deploy, SBOM CI step, and — critically — **real Postgres RLS** in Phase 1). Do NOT build certification machinery.
- **Verification-driven:** every chunk owns acceptance criteria and produces a green `verification/<area>/<AC-ID>.md` record. Iterate until verified; escalate rather than grind if an AC proves untestable or a fix would cross a safety floor.
- **Style:** no em dashes anywhere (use `--`, parentheses, or commas). No sugarcoating in docs/handoffs; be honest about half-builds.
- **Session protocol:** read `C:\dev\SESSION_PROTOCOL.md` and this project's `CLAUDE.md` at start; run `vstart`; write a handoff + `vend` at end.

---

## 7. How to start (process)

1. **Read** this brief, the handoff (`docs/handoffs/HANDOFF_2026-07-16_agentic-dev-vision-and-audit-synthesis.md`), and the audit (`docs/audits/AUDIT_2026-07-16_alpha-gap-list.md`). Skim `engine/RUNNER_CONTRACT.md` and `engine/SKILL.md` to understand the moat you're surfacing.
2. **Get Drew's Phase 0 decisions** (§3). Do not mass-build Phase 1 until scope is confirmed.
3. **Brainstorm + plan, don't cowboy.** Use the repo's own tools: `/feature-decomposition` to chunk each phase into independently-verifiable pieces with mechanical AC; write a plan and get Drew's sign-off on the Phase 1 chunk graph before implementing. This vision is big; a plan checkpoint is mandatory, not optional.
4. **Build Phase 1 first**, agent-native schema included, verification records per chunk. Point the existing frontend at the real backend as the parity proof.
5. **Do not delete `legacy/board-express/`** until its replacement reaches parity and the frontend has cut over.
6. **Reuse or prune the empty scaffolding** from the superseded brief: branches `feat/cards-api-postgres-rls` and `feat/backend-postgres-rls` (worktrees `_worktrees/backend-real` and `.claude/worktrees/feat-backend-postgres-rls`) are clean, zero-commit, at origin/main — no code to salvage, safe to reuse or remove.

---

## 8. Definition of Done (the whole effort)

The effort is "done" (v1 of the agentic-dev tool) when a human can, from the AgileCards board:
- decompose a goal into cards with machine-checkable acceptance criteria,
- watch multiple agents pull eligible cards, each in its own worktree, executing live with visible ownership/state/cost,
- see each card gated by a verifier it cannot bypass, with tier-3 merges routed to the human for approval,
- intervene on a stalled or looping agent,
- and review, per run, what it cost, how fast the feedback loop was, and which orchestration config to improve next
— all on a persistent, deployed, monitored, org-isolated backend, with the compliance cheap seams intact.

That is a tool no generic PM product can be, because it models the agent, the worktree, the token, and the gate as first-class. That is the must-have.

---

## 9. Pointers
- Handoff (full synthesis + the Agile-mechanism research with citations): `docs/handoffs/HANDOFF_2026-07-16_agentic-dev-vision-and-audit-synthesis.md`
- Audit (four-tier gap list, evidence + effort): `docs/audits/AUDIT_2026-07-16_alpha-gap-list.md` (PR #54)
- Engine specs: `engine/RUNNER_CONTRACT.md`, `engine/SKILL.md`, `engine/DEFINITION_OF_DONE.md`
- Integration roadmap (external, AgileCards is one platform tile): `C:\dev\PARADIGM_INTEGRATION_ROADMAP.md`
- Ops/status standard (external): `C:\dev\paradigm-ops\docs\standards\STATUS_REPORT_STANDARD.md`
- Live proof the market is real: this project's own worktree-per-agent + retro-cadence orchestration discipline.
