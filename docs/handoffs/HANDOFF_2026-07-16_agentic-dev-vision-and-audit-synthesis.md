# HANDOFF — AgileCards as a Must-Have Tool for Agentic Software Development

**Date:** 2026-07-16
**Author:** Opus 4.8 (audit + synthesis), on commission from Drew
**Companion files:**
- The prioritized audit: [`docs/audits/AUDIT_2026-07-16_alpha-gap-list.md`](../audits/AUDIT_2026-07-16_alpha-gap-list.md) (PR #54)
- The standalone build brief for a fresh Fable session: [`docs/briefs/FABLE_BRIEF_agentic-dev-must-have.md`](../briefs/FABLE_BRIEF_agentic-dev-must-have.md)
**Audited commit for all code claims:** `origin/main` @ `7c9ce99`
**Audience:** internal. Truth over comfort; no sugarcoating.

---

## 0. How to use these three documents

1. **The audit** (`AUDIT_2026-07-16`) is the ground truth of what exists and what's broken today, in four tiers (must-fix → powerful features), each item with evidence + effort. Trust it: every claim was verified against current code and real test/build runs.
2. **This handoff** re-synthesizes that audit and adds the strategic layer Drew asked for: what would make AgileCards a *must-have* tool for agentic software development, grounded in why Agile methodology actually works (external research, cited). It is the "why" and the "what."
3. **The Fable brief** (`FABLE_BRIEF_...`) is the self-contained "go build it" prompt for a fresh max-effort session. A Fable agent should be able to execute from that file alone, using this handoff and the audit for depth.

Read order for a human: audit → this → brief. Read order for the builder: brief (with this handoff open for depth).

---

## 1. The situation in one paragraph

AgileCards is Drew's flagship and it is in a specific, honest place: **the product is more built and more polished than a pre-alpha usually is, the path to deploying it is less built than the docs claim, and — the strategic point of this document — it is already sitting on the one asset that could make it a must-have tool for agentic software development, while its own board is blind to that asset.** The engine/runner is a real multi-agent execution engine (verified). The board is a generic human kanban wired to a legacy backend that's marked for deletion. They only talk through the filesystem. Every mainstream PM tool (Jira, Linear, Trello, Asana) assumes a human does the work. None of them models an AI agent as a first-class actor with a worktree, a token budget, a verify-gate stop condition, and non-deterministic output. That gap is the opportunity.

---

## 2. Current state (condensed from the audit — verified)

**Repo reality.** `paradigm-agilecards` is the live monorepo. `agile-cards` on disk is an empty husk; `agile-cards-board` is the superseded pre-monorepo repo. The `SUPERSEDED.md` pointer is itself stale (points at an `agile-cards/apps/board` path that no longer exists).

**The spine finding (drives half the audit).** The working, feature-rich board (kanban, triage inbox, saved views, command palette, manual rank, card-event timeline, sprint planner + capacity, 2D stakes×difficulty grid, backlog grooming) is real and wired — but **entirely to the `legacy/board-express/` backend the repo marks "delete after K11."** The new FastAPI `backend/cards_api/` is a ~490-LOC auth-guarded skeleton over an **in-memory dict** (`store.py:31`) with an incompatible card shape; the board cannot run on it. K11 (#47) shipped the JWKS auth + org-isolation *contract*, not the card-CRUD rewrite or the frontend cutover. The migration is ~10% done and labeled complete. "Delete after K11" is a landmine: acting on it deletes the running product.

**The moat (verified, and the reason this whole document exists).** `engine/runner/` is a genuine multi-agent execution engine, and it is the strongest code in the repo (713 passing tests, real thread-race and failure-path coverage). It already implements, in production-grade Python:
- **Worktree-per-agent** lifecycle: claim, heartbeat, orphan detection, reaper, worktree management (`daemon/{daemon,worktree,reaper,orphan,spawner}.py`).
- **Dependency-and-merge-aware eligibility**: a card can't be claimed until deps + merge_status + story-drift + pre-approval clear (`daemon/eligibility.py`).
- **A verifier cascade**: deterministic zero-token handlers, then a subjective haiku→sonnet→opus climb, gated on confidence, hard-capped at 2 escalations (`verifier/`, `worker_stub/sdk_invoker.py`).
- **A cost governor + reviewer-cost accounting** and **calibration** (tested in `engine/runner/tests`).
- **A tier-aware merge gate** (auto / sibling / human) and a **confidence gate** running in shadow mode (`daemon/merge_gate.py`, `daemon/confidence_gate.py`).

This is exactly the machinery an agentic-dev tool needs. The problem is that **the board doesn't see any of it** — the two halves are disconnected, and the board's own submit-story path shells out to the `claude` CLI via legacy Express, a *second, separate* execution path.

**What's genuinely good (not padding).** The board UI is polished and consistent (loading/empty/error states nearly everywhere). Test design is senior-level (1,100 tests pass repo-wide, 0 fail). Everything compiles and runs. Frontend type hygiene is strong (strict tsconfig, zero `any`). Secrets handling is done right (Infisical + env, no committed secrets). The auth/isolation code is high quality.

**Compliance seams (locked "cheap seams" posture).** 2 present (externalized secrets — strongest; FIPS-capable crypto), 2 partial (RBAC roles yes but **no RLS**; MFA seam via JWKS but no code), 4 absent (audit log, encryption-at-rest, TLS-in-repo, SBOM). Three of the absent ones only become *real* once persistence exists.

**Deploy reality.** No deploy artifact for the current stack — every Dockerfile/compose targets the frozen legacy Express app for the old host. No deploy job in CI. Deploy URL contradicts itself across three docs. The app is invisible to the `paradigm-ops` dashboard (which ingests `docs/status/*.md` via the GitHub API). "CI green" overstates protection: the live board tests run `continue-on-error` and the security-critical auth suite is not a required check.

---

## 3. The strategic thesis

**Do not build a better kanban. Build the control plane and observability surface for the multi-agent execution engine you already own.**

Three reasons this is the right bet, not a pivot:
1. **The moat already exists and is expensive to replicate.** The runner is the hard part — worktree lifecycle, eligibility, verifier cascade, cost governance. Competitors would have to build that from scratch. AgileCards has it and it's tested.
2. **The differentiator is unoccupied.** Generic PM tools model humans. "Agent-native project management" — cards that know which agent owns them, which worktree they live in, what they cost in tokens, and whether they passed a machine-checkable gate — is a category nobody owns. Drew's own operation (this multi-agent Dispatch workflow, worktree-per-agent discipline, retro cadence applied to orchestration) is the proof the need is real and the first customer.
3. **It collapses the audit's biggest problems into the vision.** Making the backend real + persistent + agent-native (audit P1) and unifying the two execution paths (audit P2) are prerequisites for the control plane *and* the top must-fix/should-fix items. The strategic build and the remediation are the same build.

**The honest constraint:** none of the agent-native features work until the foundation is real. You cannot show "cost per card" or "worktree state" on a board backed by an in-memory dict that loses data on restart and can't be deployed. **Foundation first, then the differentiators.** Sequencing is in §7.

---

## 4. The audit, four tiers (condensed — full detail in the audit doc)

The audit stands as written. Summary so this document is self-contained:

**Tier 1 — Must-fix (blocks alpha):** (M1) resolve the backend fork — product runs on the "delete-me" backend, the "real" one is empty; (M2) no deploy artifact for the current stack; (M3) contradictory deploy URL + smoke gate written for the wrong backend; (M4, conditional) marketing site ships the reverted Gantry brand and a signup form that fakes success.

**Tier 2 — Should-fix:** (S1) flip the two CI gates that make "green" overstate protection — *highest ROI in the audit*; (S2) org isolation is app-layer only, make RLS a hard requirement on the persistence work; (S3) add the audit-log seam; (S4) structured logging/error tracking; (S5) emit a `paradigm-status/v1` report; (S6) two divergent verifier trees + a false "single source of truth" doc claim; (S7) local-GPU/multi-provider not wired end-to-end; (S8) merge gate inert by default; (S9) 2–3 likely-real mypy bugs behind `continue-on-error`; (S10) SubmitStory hardcodes Drew's `C:\dev` paths; (S11) board shows "agile-cards-board" not the canonical name; (S12) frontend has no linter despite a "lint" CI job; (S13) ruff configs near-empty.

**Tier 3 — Small tweaks:** SBOM step, FIPS posture note, `/healthz` shape, cmdk scroll-into-view, SSE disconnect signal, keyboard DnD, focus outlines, stable list keys, purge ~10 MB duplicated Gantry binaries, purge 139+ Gantry strings, remove stray `engine/dashboard-v0/`, add mypy to backend, document desktop-only limitation, code-split the 509 kB bundle.

**Tier 4 — Powerful features:** this is the tier we reframe below. The audit's originals (real FastAPI+Postgres+RLS; unify the two execution paths; local-GPU end-to-end; event-sourced audit store; first-class monitoring; live-collaboration hardening; accessibility pass) all survive — but they are the *substrate* for the agentic-dev vision, not the vision itself. §5 and §6 are the vision.

---

## 5. What makes it must-have for agentic dev — each feature tied to a real Agile mechanism

This is the heart of Part 2. Drew's rule was: surface synergy as a concrete feature, grounded in *why* the Agile mechanism actually works, and say plainly where the mapping breaks. The external research (Reinertsen's flow economics, Little's Law, DORA/Accelerate, Toyota jidoka/pull, Cockburn's radiators, Brooks's Law, Google's Aristotle) is cited inline. The one-line finding that governs everything: **mechanisms grounded in math/economics transfer to agents as strong or stronger and often become literal; mechanisms grounded in human cognition/social dynamics don't transfer or invert.**

Each feature below: the mechanism → why it works → how it maps to agents (honest label) → the concrete AgileCards feature → what already exists in the repo.

### F1. Machine-checkable Acceptance Criteria as the card's literal stop condition
- **Mechanism:** *jidoka* / built-in quality (Toyota) + Definition of Done. Build quality in at each step; the line stops itself on a defect so nothing defective flows downstream, and it's fixed at source with full context. A crisp DoD is the software analogue. (Lean Enterprise Institute — jidoka; Manifesto principles 7 & 9.)
- **Mapping: STRONGEST in the whole analysis.** For a human, DoD is a social agreement that can be fudged under deadline pressure. For an agent, a machine-checkable DoD is the *literal halt condition and reward signal* — the card is done iff the verify-gate passes, and "stop the line on defect" becomes "gate fails → card doesn't merge → agent iterates or halts." This is the control that makes cheap/uneven agents *safe*: even if an agent emits garbage, a strict gate prevents merged garbage.
- **Critical caveat (build this in):** jidoka assumes the *detector* is trustworthy. The same LLM that writes weak code can write a weak or gamed test, or satisfy the letter of a check while missing intent (specification gaming). So AC must be **mechanically verifiable and adversarially robust**, and taste/architecture "done" still needs a human andon-pull. Machine-checkable DoD = strongest map; aesthetic DoD = still human.
- **Feature:** AC as a first-class card field (structured, not prose), rendered as pass/fail badges on the board; a card physically cannot enter "done" until its gate is green; a distinct "needs human review" state for taste-based criteria; a flag for "AC passed but low-confidence / possible gaming."
- **Already exists:** the verifier cascade + AC gate in `engine/runner` (deterministic + subjective). The gap is that AC isn't a first-class *card* concept on the board, and the board doesn't show gate verdicts.

### F2. Cost / tokens per card as the real unit of work (the new "story points")
- **Mechanism:** batch-size economics + a genuinely new constraint. Reinertsen: work has a holding cost; classic Agile had no per-item money meter. For agents, **tokens/compute are the primary variable cost** — the first time "effort" is directly measurable in dollars, per item, in real time.
- **Mapping: NEW constraint (classic Agile never faced it).** Story points estimated relative human effort; agent cost is *measured actual spend*. WIP becomes partly a *dollars* decision, not just a flow decision.
- **Feature:** every card accrues token + $ cost (input/output/cache), model tier used, and escalation count; the board rolls cost up per sprint/epic/agent and per run; a run-level burn-rate and budget ceiling. Replace or augment story points with measured cost.
- **Already exists:** the cost governor + reviewer-cost accounting in the runner. The gap is surfacing it on the card and rolling it up.

### F3. Agent-vs-human attribution + live lifecycle state on every card
- **Mechanism:** information radiators / visible work state (Cockburn) — make state ambient so oversight is cheap. Anderson: the board's job is to make WIP and queues visible so bottlenecks can't hide.
- **Mapping: STRONGER for the human overseer, DOESN'T-MAP agent-to-agent.** A human supervising 8 parallel agents has *zero* natural peripheral awareness of what they're doing, so an explicit radiator (which agent/model/tier owns which card, lifecycle state, liveness/heartbeat) is the *only* way to keep oversight — more necessary than for a co-located human team. But Cockburn's actual mechanism is *osmotic* (humans absorbing ambient info), and agents don't overhear each other or build tacit shared context. Inter-agent context must be **explicitly plumbed** (shared spec files, a coordinator passing state) — there is no osmosis to lean on.
- **Feature:** each card shows owner (agent instance + model + tier, or human), lifecycle state (queued → eligible → claimed → executing → verifying → in-review → merged → failed/abandoned), and heartbeat/liveness; a stalled agent is visually flagged (the runner already detects orphans). Do **not** rely on agents "seeing" the board — plumb any inter-agent dependency through explicit shared artifacts.
- **Already exists:** claim/heartbeat/orphan/reaper in the daemon. The gap is entirely the board surface.

### F4. Worktree-aware cards
- **Mechanism:** CI/trunk-based development as a *precondition*, not a nicety (DORA/Accelerate). Parallel agents on long-lived divergent branches are a merge-storm generator; short-lived worktree branches + CI are what make parallelism safe.
- **Mapping: STRONGER, load-bearing.** For agentic dev the *unit of isolation is the git worktree*. A board that doesn't model worktrees is blind to where the work physically lives. DORA's "long-lived branches wreck flow" finding is amplified when the branch authors are machines generating code at speed.
- **Feature:** each executing card maps to a worktree + branch; the card shows worktree path, branch, diff size (lines/files), PR link, and CI status; an integration/merge queue view; a warning when a card's branch diverges too far from trunk.
- **Already exists:** the runner's worktree management + PR lifecycle. The gap is board visibility and a merge-queue surface. (Note: worktree-per-agent is *already Drew's live discipline* — this feature productizes a practice that's already proven here.)

### F5. Concurrency limits as cost + merge-conflict + review-capacity control (WIP reframed)
- **Mechanism:** Little's Law — cycle time = WIP ÷ throughput; every extra item started lengthens everything. Capping WIP forces completion over initiation. (Little 1961 via Anderson.)
- **Mapping: SAME MATH, INVERTED BOTTLENECK.** For humans, WIP limits protect focus and fight the ~20%/task context-switch tax (Weinberg — heuristic). Agents don't lose focus by having siblings; agent WIP caps protect (a) **token/compute cost in parallel**, (b) **merge-conflict surface** (N agents on an overlapping tree ≈ O(N²) collisions), (c) **the human reviewer's serial capacity** — the real bottleneck — and (d) the orchestrator's own context window. The constraint is arguably *tighter* than for humans, but it lives in cost + integration + review queue, not cognition.
- **Feature:** WIP/concurrency limits per column/lane that cap *concurrent agents*, tied to a token budget and to review-queue depth (not to "focus"); the board shows current concurrency vs cap and the binding constraint (cost? conflicts? review backlog?). Pull the next card only when a slot frees *and* the budget allows.
- **Already exists:** the runner has worker slots. The gap is tying the cap to cost + review capacity and showing it.

### F6. Pull-based dispatch on *verified* completion + dependency eligibility
- **Mechanism:** pull scheduling / JIT (Toyota's second pillar) — downstream capacity signals when to release work; caps WIP endogenously; matches release rate to *actual* completion. (Reinertsen on queue management.)
- **Mapping: SAME / STRONGER, and literal.** An orchestration loop *is* a pull system: dispatch the next card only when a worker slot frees. Cleaner than with humans because the completion signal is machine-observable. The theory's one demand: match to *real* completion — so the trigger must be the **verified** stop condition (gate passed), not merely "agent returned." Push scheduling (fire all cards at once) reproduces exactly the queue-explosion and merge-storm the theory predicts.
- **Feature:** a scheduler that pulls the next *eligible* card (deps merged, pre-approved) into a free slot, and treats "done" as gate-verified, not agent-returned; the dependency DAG is visible on the board and "eligible now" is a first-class state.
- **Already exists:** eligibility (deps + merge_status + drift + pre-approval) and daemon dispatch; a `DependencyView` component exists in the frontend but isn't wired to the runner. The gap is wiring them together and surfacing eligibility.

### F7. Verify-gate latency as a first-class flow metric
- **Mechanism:** fast feedback loops (Reinertsen: the primary economic payoff of small batches; defect cost escalates with detection latency — NASA cost-escalation study; the specific "10×/100×" multipliers are weakly sourced, the *direction* is solid).
- **Mapping: STRONGER — where agents win biggest.** The feedback loop can be *fully automated and machine-checkable*, so the agent iterates against the signal with no human in the loop. The human context-decay driver is absent (a window doesn't decay over calendar time) but the window is finite, so fast feedback still matters — it lets the agent correct *before it exhausts its context or drifts*. The binding requirement becomes the **quality** of the automated signal: a slow or flaky verify-gate is far more damaging to an agent (which can't smell that something's off) than to a human.
- **Feature:** measure and surface per-card verify-gate latency and the run's feedback-loop time (claim → verdict); flag flaky/low-signal gates explicitly; treat gate latency as a flow metric to optimize, the way DORA treats lead time.
- **Already exists:** the verifier runs; latency isn't measured/surfaced, and there's no flaky-signal detection.

### F8. Direct batch-size / card-scope constraints
- **Mechanism:** batch-size economics (Reinertsen B11). Optimal batch size falls as transaction cost falls — and agent tooling drives transaction cost toward zero (automated branch/CI/gate), which pushes the optimum *small*.
- **Mapping: STRONGER, with an inverting caveat.** With humans, "small deploy" reliably implied "small change." **An LLM can emit a large sprawling change as a single card**, so a healthy-looking cadence can hide an oversized batch, and DORA's deploy-frequency-as-batch-size proxy becomes *unreliable*. For agents you must constrain batch size **directly** (scope/files/LOC/AC-count per card), not infer it from frequency. The dominant holding cost also changes: a big card blows the agent's context window and drifts from spec, rather than boring a human.
- **Feature:** enforce card-scope limits (max files/LOC/AC per card); flag oversized cards; offer auto-decomposition (the repo already has a `/feature-decomposition` skill) to split a too-big card before an agent claims it.
- **Already exists:** the decomposition skill exists as a separate tool; nothing enforces card scope or wires decomposition into the card lifecycle.

### F9. Orchestration retro / kaizen analytics
- **Mechanism:** kaizen (Toyota) — a recurring loop that treats the *process itself* as the improvement target, empirically and in small steps. Substrate-neutral. (Separate from the human-retro ritual, whose effectiveness is gated by psychological safety — Google Aristotle.)
- **Mapping: kaizen STRONGER; the human-safety framing DOESN'T-MAP.** The improvement *target* becomes prompts, tool definitions, verify-gates, decomposition patterns, and tier-routing policy — and unlike a human team you can **version them in git, A/B them, and roll them back**: kaizen with reproducibility. But the psychological-safety / blameless / morale apparatus solves a problem agents don't have. You don't run a retro *with* the agents to make them feel safe; the human inspects outcomes and edits the system. Attributing "the agents reflecting" is a category error.
- **Feature:** a retro view over card outcomes — verify pass-rate, escalation frequency, cost, and rework rate, sliced by card-type / prompt version / model tier — that points at *which config to change*; changes to prompts/gates/AC are versioned artifacts with before/after metrics.
- **Already exists:** calibration, the confidence gate's shadow data, and a signals/metrics ledger in the runner. The gap is the retro/kaizen view and treating orchestration config as versioned, measured artifacts. (Note: this project *already runs a retro-cadence practice on its own orchestration* — the feature productizes an existing internal habit.)

### F10. Human supervision console (andon + intervention + tier-3 approval)
- **Mechanism:** jidoka's andon (stop-the-line authority) + Brooks's Law relocating onto the human. Brooks's Law doesn't vanish with agents; it *mutates* — context-engineering + review + debug overhead make the **human orchestrator the O(N) bottleneck** feeding and reviewing N agents (Brooks 1975; Forret, "The Mythical Agent-Month" — plausible, no hard numbers). Taste-based DoD still needs a human (F1 caveat).
- **Mapping: STRONGER need, and the bottleneck is explicit.** Since the human reviewer is the binding constraint (F5), the tool's job is to make *their* work cheap: one place to approve tier-3 merges, review agent diffs, and intervene on stalled/looping agents (kill, re-queue, escalate tier, pull the andon). Optimizing the review surface is optimizing the system's actual throughput bottleneck.
- **Feature:** the board is the supervision console — tier-3 merge approval as a board action, an agent-diff review surface, and controls to kill/re-queue/escalate a stuck or looping card; a "needs human" queue that's explicitly the throughput bottleneck to watch.
- **Already exists:** the merge gate's tier routing (auto/sibling/human). The gap is that the human gate isn't a board action and there's no intervention UI.

### F11. Non-determinism / rework tracking
- **Mechanism:** a genuinely new constraint. Classic Agile assumed a deterministic-enough worker; the same card + same prompt can yield different agent output, which weakens every reproducibility assumption and makes flaky-vs-real signal hard to distinguish.
- **Mapping: NEW.** No Agile precedent.
- **Feature:** track attempts/retries/variance per card; surface rework rate and a specification-gaming flag (AC passed but reviewer/heuristics suspect the check was gamed); distinguish "flaky gate" from "genuinely failing work."
- **Already exists:** nothing directly; the calibration data is the closest substrate.

### F12. Fleet delivery metrics (DORA for agents)
- **Mechanism:** DORA/Accelerate's four keys (lead time, deploy frequency, change-fail rate, MTTR) — empirically predictive of delivery performance. (Forsgren/Humble/Kim 2018.)
- **Mapping: SAME, applied to the agent fleet — with the batch-size-proxy caveat from F8.** Lead time (card created → merged), throughput, change-fail rate (gate-passed cards later reverted), and recovery time map cleanly to an agent fleet; deploy-frequency alone is a *less trustworthy* batch-size proxy for machine-generated change, so pair it with F8's direct scope metric.
- **Feature:** a fleet dashboard — lead time, throughput, change-fail, MTTR — per run/sprint/agent; wired to emit a `paradigm-status/v1` report so the fleet shows up in `paradigm-ops` (also closes audit S5).
- **Already exists:** the signals ledger is raw material; no fleet dashboard, no status report.

---

## 6. Where Agile does NOT map — build these differently or not at all

Drew asked explicitly for honesty here. These are human-team artifacts; forcing them onto agent orchestration is where "agentic agile" goes wrong.

- **Fixed sprint cadence / timeboxes → mostly vestigial.** The calendar sprint is a human coordination scaffold (humans batch planning/review because they can't be interrupted arbitrarily). Agents have near-zero setup cost and run continuously, so the natural mode is **single-piece continuous flow** — the purer lean ideal human teams could only approximate. *Keep* the synchronization purpose (integration/review windows), but trigger it by **human review capacity and merge windows**, not a two-week clock. Do not make sprint ceremony the core loop. (AgileCards has a sprint planner; keep it as an optional human-facing framing, not the engine's heartbeat.)
- **Psychological-safety / blameless retros / morale → doesn't map.** Agents have no fear to relieve, no ego, no candor to unlock. Build kaizen analytics (F9), not a "team retro" ritual with the agents. Don't anthropomorphize.
- **Osmotic / face-to-face communication → doesn't map.** No overhearing, no co-location benefit, no tacit shared context. All inter-agent context is explicitly plumbed (F3). Drop the osmosis theory; keep the radiator.
- **Sustainable pace / "constant pace indefinitely" → different mechanism entirely.** No agent fatigue. The analogous constraint is a **token/compute budget and rate limits** — a cost ceiling, not a wellbeing one. Same principle slot, unrelated mechanism; model it as budget (F2/F5), not "don't overwork the agents."
- **Self-organizing / motivated / autonomous teams → doesn't map.** No durable ownership, no motivation, no self-organization toward business goals; "autonomy" is bounded prompt + gate. The coordination cost these ideas managed *relocates onto the human orchestrator* (F10) — optimize that, don't pretend the agents form a team.
- **Story points as relative human effort → replace, don't port.** Points estimated human effort under uncertainty. For agents, measured token cost (F2) is strictly better — actual, in dollars, in real time. Keep points only as an optional human-planning overlay.

**The net-new bottleneck the whole system rotates around is the human reviewer + token budget + merge integration — not the worker's focus.** Every feature above should be evaluated against: does it make *that* bottleneck cheaper?

---

## 7. Sequencing — foundation first, then the differentiators

The agent-native features are worthless on a backend that can't persist or deploy. Order:

**Phase 0 — Decisions (Drew; minutes, unblocks everything).** See §8.

**Phase 1 — Real, persistent, agent-native backend (the audit's P1, done right).** Postgres + full card CRUD + RLS. *Critically: design the schema agent-native from day one* — cards carry agent/human owner, lifecycle state, worktree/branch ref, token+cost, structured AC, attempt/rework count, tier. Retrofitting these later is far more expensive than baking them into the first schema. This single phase closes M1-partial, S2 (RLS), and lays the substrate for F1-F4, F11. Effort: L-XL. *This is what the paused Fable session was starting; its brief was narrower (backend only) and is superseded by the agent-native schema requirement.*

**Phase 2 — Deploy + monitoring (audit M2/M3/S4/S5).** Dockerfiles for the real stack, one canonical URL, structured logging, and a committed `paradigm-status/v1` report (also F12 groundwork). Effort: M.

**Phase 3 — Unify the execution paths + wire the board to the runner (audit P2).** Route the board's submit-story through the engine runner (kill the second `claude`-CLI path), and make the board read the runner's live state. This is the moment the board stops being a generic kanban. Delivers F3 (attribution/lifecycle), F4 (worktree state), F6 (pull + eligibility, wiring the existing `DependencyView`). Effort: L.

**Phase 4 — The agent-native control surface.** F1 (AC as stop condition + badges), F2 (cost per card + rollups), F5 (concurrency as cost/review control), F7 (gate latency), F10 (supervision console). Effort: L.

**Phase 5 — Intelligence layer.** F8 (scope limits + auto-decompose), F9 (kaizen analytics), F11 (rework/gaming tracking), F12 (fleet DORA dashboard). Effort: L, and the most differentiating.

Small-tweak and cheap-seam items from the audit (S1 CI gates, S3 audit-log, S6 verifier dedup, Gantry purge, naming) fold in opportunistically; S1 (flip the CI gates) should happen before any of this so alpha-period regressions can't merge green.

---

## 8. Decisions still open (Drew's calls — options + reasoning)

These are unchanged from the audit and gate Phase 0:

1. **Which backend does alpha ship on while Phase 1 is built?** Option A (legacy Express now — works, persists, fast, but bypasses the audited auth and is the code marked "delete"); Option B (FastAPI — strategic but empty, XL to parity, loses data on restart); Option C (hybrid — legacy CRUD behind the FastAPI gate, most integration complexity). *Recommendation:* A for a trusted-tester alpha, with Phase 1 as the committed follow-through; relabel legacy from "delete" to "active until Phase 1" to defuse the landmine.
2. **Is the marketing landing in alpha scope?** If no, cut the route (removes M4). If yes, it needs a re-brand + a real form.
3. **Does alpha claim local-GPU execution?** If yes, S7/audit-P3 becomes near-blocking (the tool-using local path is unbuilt). If no, defer cleanly.

New decision surfaced by this synthesis:

4. **Is the agentic-dev-tool vision the committed product direction, or an option to weigh?** This document assumes it's the direction and sequences accordingly. If it's still a bet Drew wants to pressure-test, that's a `/council` candidate before Phase 3+ (Phases 1-2 are needed regardless).

---

## 9. State left behind by this session

- **Audit** committed and PR'd: `docs/audits/AUDIT_2026-07-16_alpha-gap-list.md`, PR #54 (open, discussion artifact, not for merge until Decisions 1-3).
- **This handoff + the Fable brief** committed on branch `docs/agentic-dev-vision-2026-07-16`.
- **Paused Fable sessions** `local_d7d09d8d…` and twin `local_c5d51f34…` ("Build real AgileCards backend") created branches `feat/cards-api-postgres-rls` and `feat/backend-postgres-rls` (+ worktrees `_worktrees/backend-real` and `.claude/worktrees/feat-backend-postgres-rls`, the latter locked). **Both are empty — clean, zero commits, at origin/main.** No code to salvage. They confirm P1 as the target. Safe to reuse for Phase 1 or prune; left untouched because the sessions still show running and their worktrees aren't mine to discard.
- **Repo hygiene flag (unchanged from audit):** the primary checkout `C:\dev\paradigm-agilecards` is dirty on the stale `fix/readme...` branch, local `main` 8 behind origin, with already-merged K11 leftovers uncommitted. Wants a `git switch main && git pull` + discard of the duplicate untracked files. Not touched (not mine to discard).
- Audit worktrees from the first session were cleaned up; this session's `_worktrees/vision` worktree should be pruned after the docs land.

---

## 10. Pointers

- Prioritized audit: `docs/audits/AUDIT_2026-07-16_alpha-gap-list.md`
- Fable build brief: `docs/briefs/FABLE_BRIEF_agentic-dev-must-have.md`
- Integration roadmap (external): `C:\dev\PARADIGM_INTEGRATION_ROADMAP.md` (the 126-AC platform v1; AgileCards is one tile)
- Engine specs: `engine/SKILL.md`, `engine/RUNNER_CONTRACT.md`, `engine/DEFINITION_OF_DONE.md`
- Board docs: `docs/board/DASHBOARD_ROADMAP.md`
- Ops dashboard + status standard (external): `C:\dev\paradigm-ops` (`docs/standards/STATUS_REPORT_STANDARD.md`)
- The live example: this project's own worktree-per-agent discipline and retro cadence applied to agent orchestration — the first proof the tool's market is real.

---

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md` in this project, then this handoff, then the audit (`docs/audits/AUDIT_2026-07-16_alpha-gap-list.md`), then run `vstart`. If you are the Fable session commissioned to build, your complete brief is `docs/briefs/FABLE_BRIEF_agentic-dev-must-have.md` — start there, use this handoff for depth.
