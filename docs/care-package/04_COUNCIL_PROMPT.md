# The Question Posed to Council (verbatim)

**Date:** 2026-07-16
**Mode:** `/council deep`
**Preserved because:** Drew asked that the original prompt/question posed to the council be part of the care package, alongside the raw transcript and the synthesis, so the deliberation can be audited against what was actually asked.

**Inputs handed to the council:** `01_RECONCILIATION_MEMO.md`, `02_REVISED_HANDOFF.md`, `03_AGILE_AGENTIC_RESEARCH.md`, and `docs/audits/AUDIT_2026-07-16_alpha-gap-list.md` (PR #54).

---

## The prompt

> **Topic: Should AgileCards commit to the board-as-control-plane-for-the-agent-engine pivot, and given the reconciliation, what is the right sequencing and final scope?**
>
> ### Context
> AgileCards (`Ginkobaloba/paradigm-agilecards`) is Drew's flagship, heading to alpha. A verified six-lens audit established: the polished board runs entirely on a `legacy/board-express/` backend the repo marks "delete after K11"; the intended FastAPI replacement was an auth-only skeleton over an in-memory dict; no deploy artifact exists for the current stack; 1,100 tests pass but two CI gates make "green" overstate protection. Separately, the repo owns a genuinely strong asset: `engine/runner/`, a mature multi-agent execution engine (worktree-per-agent claim/heartbeat/orphan/reaper, dependency+merge eligibility, a verifier cascade with confidence-based tier climb, a cost governor, tier-aware merge gating; 713 passing tests).
>
> **The proposed pivot:** do not build a better human kanban; make the board the control plane and observability surface for the execution engine the repo already owns. No mainstream PM tool (Jira/Linear/Trello) models an AI agent as a first-class actor with a worktree, a token budget, a machine-checkable stop condition, and non-deterministic output. The differentiating, currently-unowned scope is: AC as a first-class card field with gate badges (F1), agent/human attribution + lifecycle (F3), worktree-aware cards (F4), concurrency limits tied to cost + review capacity (F5), verify-gate latency as a flow metric (F7), direct card-scope limits (F8), and a human supervision console (F10). Each traces to a real Agile mechanism (see `03`), not an analogy.
>
> ### What reconciliation established (see `01`)
> Three sessions were building in parallel:
> - **Two duplicate sessions** independently built Postgres+RLS backends. Careful work, converged on the same architecture. But both were briefed on the audit's *narrow* P1 (legacy wire parity), and their `0001` migration carries **zero** agent-native model (`agent`/`worktree`/`cost`/`attempt`/`AC`/`confidence`/`provider` = 0 hits). They do not merge (both authored their own `0001`; 8 conflict signals).
> - **A K/L/S/P track session**: Track L (provider-agnostic execution) has KL1/KL2 merged and KL3 in progress; **Track S** (sprint → orchestrator wire) and **Track P** (portal federation) have not started. Drew locked sequencing **L → S → P, sequential**, on 2026-07-15.
> - **My proposed Phase 3** ("unify execution paths, wire board to runner") is **substantially redundant with Track S**, which owns it and is better scoped.
>
> ### The two fault lines the council must resolve
> **FAULT LINE 1 -- the card store is split-brain, and one side already picked a winner unilaterally.**
> The engine's documented, live model: card files on disk (`C:\dev\todo\`) **are the source of truth**; the runner claims from a filesystem tree; the board watches the tree. Zero coupling -- a real architectural strength.
> The backend's built model (Branch A): cards are **Postgres rows** and the file path is a *synthetic derived property* (`f"{STATUS_FOLDERS[self.status]}/{self.id}.md"`), plus a one-way file→Postgres import.
> Both are live. Neither session knows the other made the call. **Track S is the wire between sprints (Postgres) and the orchestrator (filesystem) -- precisely where the contradiction detonates.**
> Options, none free:
> - **(a) Postgres authoritative**, runner reads/writes the DB. Single truth; costs the engine its zero-coupling independence and its plain-file-tree operation.
> - **(b) Filesystem authoritative**, Postgres a projection/read-model. Preserves the engine's independence and the cards-are-files property that makes worktree-per-agent and git-native workflow natural; costs a sync/projection problem and a subtler RLS write path.
> - **(c) Split by domain** -- customer/board cards in Postgres, operator/runner cards on disk, with a narrow versioned contract. Honest that these are two products; costs two card models forever and undercuts the one-board vision.
> - **(d) Event-sourced** -- one append-only log as truth, DB and file tree both projections. Most satisfying, most expensive, likely overkill for alpha.
>
> **FAULT LINE 2 -- `0001` carries no agent-native model.** Some scalars could ride in `frontmatter` JSONB, and adding an `agent_runs`/`card_attempts`/cost-ledger table in `0002` is a normal *additive* migration (evolution, not rewrite) -- so this alone may not force a rebuild. But you cannot sensibly design the agent-native card model until you know **which store owns a card**.
>
> ### Drew's governing principles (binding on the deliberation)
> 1. **Done properly from the beginning** -- prefer a clean, correct build over force-salvaging partial/rushed work. (Note: Branch A is *not* rushed -- it found and fixed a real packaging bug, fails loudly rather than skipping to avoid CI-masking, expanded lint/mypy. Its problem is *brief*, not quality.)
> 2. **Version increments are evolutions of one codebase, never rewrites.** A design flaw found now gets fixed now; deferred architectural debt compounds into a forced rewrite once features interconnect on top of it.
> 3. **The binding test, which you must apply explicitly to every phase of BOTH plans (mine AND K/L/S/P):** not "does this phase work," but **"will this phase's design still be standing, unmodified, three phases from now?"** Anywhere the honest answer is no, flag it and resolve it now rather than logging it as a v-next TODO.
>
> ### What I need from the council
> 1. **The pivot: commit, or not?** Is board-as-control-plane the right strategic bet, or is it a solution looking for a problem? Argue the strongest case *against* it too -- including "this is an internal operator tool and Drew is over-indexing on his own workflow being a market."
> 2. **Fault Line 1: pick (a)/(b)/(c)/(d)** and defend it. This is the highest-stakes call in the document.
> 3. **Fault Line 2:** re-cut `0001` now, or land legacy-parity and evolve additively? Apply principle 2 honestly.
> 4. **Branch A: keep as pattern+code source with a re-cut `0001`, adopt wholesale, or rebuild clean?** Principle 1 cuts both ways -- it forbids force-salvaging rushed work *and* gratuitously retyping correct work.
> 5. **Sequencing and final scope** across the merged plan (Phases 0-4 + Track L/S/P), with **one owner per item** and nothing built twice.
> 6. **Apply the three-phases-out test to Track L, Track S, Track P, and each of my phases.** Name every place the honest answer is "no." Note my prior read: Track L largely *passes* (the provider port is deliberately neutral/extensible -- `ToolSpec` = name + schema + executor, designed so a future non-code tool type plugs in without rework). Contest that if it's wrong.
>
> **Do not sugarcoat. Where the honest answer is "I don't know" or "the evidence doesn't support this," say so. Drew's standing rule is truth over comfort.**
