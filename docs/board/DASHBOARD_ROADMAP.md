# Dashboard Roadmap

Status: **draft for review** -- Drew, this is a proposal, not a decided plan.
Where there's a real product fork, I've called it out as an option rather than
picking unilaterally. See the "Forks for Drew to decide" section near the end.

Date: 2026-05-20
Author: Claude (planning session, dispatched via orchestrator)
Repo: `agile-cards-board`

---

## 1. What this dashboard is for

`agile-cards-board` is the human cockpit for an AI-agent workforce. Behind it
sits a filesystem-backed card store (the `/cards` skill) and an autonomous
runner daemon that dispatches cards to Claude executors. Every card carries a
real dollar cost (LLM tokens) and a cost-cap governor exists to keep that
bounded.

**North star.** Make a solo developer (or a 2-3 person team) as effective as
a team five times that size, by leaning aggressively into agentic AI. The
dashboard's job is to keep humans fully oriented to what the agents are
doing, what they cost, what's queued, what needs review, and what to plan
next, so that resources stay fully utilized and quality stays high.

**Three levels of goal**, in Drew's framing:

1. **Goals being completed** -- near-term, feasible fast, table stakes.
2. **Goals being worked on** -- mid-term, where the dashboard starts
   genuinely earning its keep as a planning surface.
3. **Goals to strive toward** -- long-term, the agentic-AI-maximizing
   features that turn this from "a Kanban over markdown" into "the right
   UI for running an AI workforce."

This document walks each horizon, then catalogs every feature as a single
table at the end so you can rank/cut quickly.

---

## 2. Current-state audit

What actually exists today, on the worktree branch
(`Nexus/jovial-einstein-4b9732`), which contains the latest polish work.
Branches `feature/dashboard-polish`, `feature/submit-story-surface`,
`chore/sync-main-with-dashboard-polish`, and `consolidate/main-polish-and-tunnel`
are unconsolidated variants of the same feature set -- they don't add new
surfaces beyond what's here. (A separate session is consolidating those
branches; this roadmap doc is the only artifact I touch.)

### What works

- **5-column kanban** (Backlog, Active, In Review, Done, Blocked) backed by
  the on-disk `todo/` tree. Columns come from the API so the backend stays
  the source of truth for status set.
- **Drag-and-drop** between columns via `@dnd-kit`, with optimistic UI
  updates and a server reconciliation step on the SSE echo. Backed by an
  atomic file rename + frontmatter `status:` rewrite in the backend.
- **Bearer-token auth** with a SHA-256-hashed token store, CLI scripts to
  mint/list/revoke. Tokens land in `sessionStorage`, so a tab close logs
  you out.
- **Live updates** end-to-end. `chokidar` watches the card tree, the
  backend's event bus fans changes out over SSE, the `useSSE` hook patches
  the Zustand store, and two tabs converge inside a few hundred ms.
- **Submit-story surface** (`/submit`). Full backend route, SSE streaming
  of the planner subprocess, dry-run review panel showing the staged
  cards with tier histogram and dependency edges, and Approve / Cancel
  promotion to the backlog. This is partial auto-decomposition already.
- **Card detail modal** with the full frontmatter table and markdown body
  rendered via `react-markdown` + `remark-gfm`.
- **Card tile dense layout**: tier badge, stakes pill, extended-thinking
  pill, pin-required pill, model name, short-id (`b042-01`).
- **Rich card schema** on disk. Every card already carries `points`,
  `stakes`, `difficulty`, `model`, `model_floor`, `extended_thinking`,
  `pin_required`, `cost_cap_usd` (currently always `null`),
  `estimated_tokens`, `actual_tokens`, `estimated_duration_minutes`,
  `actual_duration_minutes`, `depends_on`, `touches`, `batch`,
  `claimed_by`, `started_at`, `finished_at`, `model_used`,
  `last_heartbeat`, `branch`, `base_branch`, `merge_status`,
  `verified_at`, `verified_by`, `cascade_history`, etc. The data is all
  there; the dashboard surfaces a fraction of it.
- **Sprint + retro backends**: SQLite schema in place (`sprints`,
  `sprint_cards`), routes wired (`GET/POST /api/sprints`,
  `POST /api/sprints/:id/cards`, retros likewise). No UI yet on either.
- **Cloudflared tunnel** hosting at `app.projectNexusCode.org`,
  documented in `docs/cloudflared-tunnel.md` and a persistent-tunnel
  migration checklist.
- **Demo seed script** (`backend/scripts/seed-cards.ts`) producing a
  believable two-project, multi-batch slice of cards so the board renders
  populated without a live runner.
- **Tests**: vitest on the frontend, node:test on the backend
  (`stories.test.ts`).

### What does not exist

- **No search.** No Cmd-K, no input field, no search-by-title, no search
  in card bodies. The header has Refresh and Sign out, and that's it.
- **No filtering.** Cards render unfiltered into status columns; the only
  reduction is `cardsByStatus`. There's no by-project, by-batch,
  by-assignee/runner, by-tier, by-stakes, by-pin, by-`extended_thinking`,
  or by-`merge_status` filter. The frontmatter has all of those fields;
  none are filter-bound.
- **No saved views.** No view abstraction at all yet.
- **No sort or rank.** Within a column, cards sort alphabetically by id.
  No manual rank, no drag-to-reorder within a column, no sort-by-anything.
- **No multi-select.** One card at a time, every action.
- **No dollar-cost surface anywhere.** `cost_cap_usd` is `null` on every
  seed card; the field exists but no UI computes, displays, sums, caps,
  warns, or budgets on it. Tokens are recorded
  (`estimated_tokens`/`actual_tokens`) but never rendered as dollars.
- **No agent / runner observability.** `claimed_by`, `last_heartbeat`,
  and `model_used` are on every card; nothing in the UI lists "agents",
  shows what each runner is doing right now, or surfaces a stalled
  heartbeat. The card tile shows `model`, not `claimed_by`.
- **No sprint planner UI.** `frontend/src/routes/SprintPlanner.tsx` is
  a placeholder that says "v1 coming soon."
- **No retros UI.** Same story.
- **No backlog-grooming surface** distinct from the kanban view. The
  Backlog column is the grooming surface, which conflates "ready to
  run" with "not yet refined."
- **No dependency view.** `depends_on` is parsed by `parseCard.ts` but
  never rendered as a graph or even as a "needs X" badge on the tile.
- **No WIP limits, no swimlanes, no grouping.**
- **No keyboard shortcuts** beyond what dnd-kit and Radix Dialog give
  you for free.
- **No notifications, mentions, or comments.**
- **No cost-cap governor logic in the backend.** The field is reserved
  for it, no enforcement path exists yet.
- **No per-card event timeline.** SSE events are consumed to update the
  board, but per-card runner output (claim, heartbeat, finish, verify,
  cascade) is not surfaced as a stream the user can read.

The honest summary: the substrate is excellent (rich schema, live updates,
auth, the drag-drop primitive, the submit-story decomposition pipeline),
and the **viewing/planning/oversight surface is thin**. Everything below
is about closing that gap, in three deliberate horizons.

---

## 3. Horizon 1: Near-term (goals being completed)

**Definition.** PM table stakes. Patterns that mainstream tools have
converged on for good reasons. Anyone who lands on the board expects to be
able to do these. Total effort estimate: ~3-5 weeks for a single developer
working with Claude as a force multiplier, ~1-2 weeks of that being
keyboard-and-polish.

Each item is ranked by recommended order.

### 1.1 Command palette (Cmd-K) with global fuzzy search

A single keyboard-triggered palette that mixes navigation + commands +
fuzzy search across cards. Recently-used items at the top, fuzzy match
on title and id (full-body grep is a nice-to-have but not required for
v1 -- the dashboard has hundreds of cards, not tens of thousands).

Why first: it's the single highest-perceived-sophistication add for the
lowest build cost. Every modern PM tool converged on this for a reason.

**Effort: M** (one focused day for the shell, another day for
keyboard handlers and theming). **Depends on:** nothing.

### 1.2 Chip-based filter bar above the board

A row of dismissable filter chips above the columns: project, batch,
assignee/`claimed_by`, tier, stakes, `pin_required`, `extended_thinking`,
`merge_status`. Each chip opens a popover of available values from the
loaded cards. Multi-select within a chip is union; across chips is
intersection.

Why a chip builder and not a query DSL: even Atlassian is walking back JQL
with natural-language-to-JQL because users hate writing it. For a small
board, chips win.

**Effort: M.** **Depends on:** nothing.

### 1.3 Per-card dollar-cost chip on the tile

The novel one. None of the surveyed PM tools (Linear, Jira, Trello,
Height, Notion, GitHub Projects, ClickUp) display per-card dollar cost
because their work isn't priced per task. Yours is.

- Show `$est` on a backlog card (computed from `estimated_tokens`
  &times; per-model rate).
- Show `$spent` on an active or done card (from `actual_tokens`).
- Color-step the chip toward warning at ~80% of `cost_cap_usd`, danger
  at 100%.
- Sum per-column cost in the column header (alongside the existing
  count badge).

The model-to-rate table lives in the backend so we can recompute history
when rates change. Expose as a small JSON endpoint
(`GET /api/rates`) and cache on the frontend.

Why this matters: cost visibility turns "we have a runner" into "we have
a budget," and it's the prerequisite for everything in horizon 3.

**Effort: M.** **Depends on:** nothing for v1 (display only);
horizon 3 cost-cap governor builds on this.

### 1.4 Manual rank within column, drag-to-reorder

Today columns sort alphabetically by id. Add a drag-to-reorder gesture
inside a column, with rank stored in SQLite (not in frontmatter -- the
disk file is the work definition, rank is a fast-changing UI concern).
Sort dropdown on the column header: Rank (default), Created, Tier, Cost,
Heartbeat.

**Effort: M.** **Depends on:** small backend addition (`card_rank`
table). Fork: see "Forks" section A.

### 1.5 Saved views

A "view" is (filters + grouping + sort + columns). Save with a name,
recall from a sidebar list, share via URL. Default view is "everything,
ranked." Persist per token (token id is your user proxy until you build
multi-user).

Critically: a saved search IS just a saved view with filters. Don't build
both abstractions.

**Effort: M-L.** **Depends on:** 1.2 (filters), 1.4 (sort).

### 1.6 Keyboard parity for the common actions

Every drag has a shortcut. `S` opens a status picker on the focused card,
`/` focuses search, `F` opens the filter bar, `Cmd-K` opens the palette
(redundant but expected), `X` to select, `Shift-S` to move to current
sprint (once 2.1 lands), `Esc` to clear.

Why bother early: it's much cheaper to design keyboard alongside the
drag UX than to bolt it on after. Linear's bet -- and it's a good one.

**Effort: S** (assuming we ride on Radix for popovers).
**Depends on:** 1.1, 1.2, 1.4.

### 1.7 Tile polish: short-id click-to-copy, age, dependency badge

- Short id (`b042-01`) becomes click-to-copy with a tiny confirmation.
- Tile shows "2h ago" / "stale 4d" derived from `mtimeMs` for active
  cards.
- If `depends_on` is non-empty and any dep is not `done`, show a "blocked
  on N" badge (clicking jumps to the dep).

Small, high-perceived-quality.

**Effort: S.** **Depends on:** nothing.

### 1.8 In-column count + cost rollup

Column header today shows `count`. Add `$ spent` or `$ est` next to it.
For Done, show actual; for Active/Backlog, show estimate.

**Effort: S.** **Depends on:** 1.3.

### Near-term summary

After horizon 1, the board feels like a real PM tool: searchable,
filterable, sortable, keyboard-driven, and cost-aware. Drew has
explicitly named most of these.

---

## 4. Horizon 2: Mid-term (goals being worked on)

**Definition.** The leap from "PM tool" to "sprint planning cockpit
where resources stay fully utilized." This is where the dashboard starts
earning its rent. Total effort estimate: ~6-10 weeks.

### 2.1 Sprint planner UI (the real one)

Backend already speaks `GET/POST /api/sprints` and
`POST /api/sprints/:id/cards`. Build the timeline:

- Sprints render as columns with start/end dates and budget meters.
- Drag a card from Backlog onto a sprint, persists via the existing API.
- Per-sprint **tier budget** (sum of `points`).
- Per-sprint **dollar budget** (sum of estimated $).
- Per-sprint **goal** (a text field stored on the sprint).
- "+1 / +2 / +3" future sprints visible so multi-sprint pre-planning
  works.
- Carry-over policy at end-of-sprint: incomplete cards auto-roll to
  `Sprint +1` (Linear cycle pattern). Toggle to off for teams that
  prefer manual.

**Effort: L.** **Depends on:** 1.4 rank (so backlog ordering is
meaningful when pulling into a sprint).

### 2.2 Capacity model: min(agent slots, $-budget, review bandwidth)

A first-class capacity number per sprint, defined as the smaller of:

- **Parallel agent slots** -- how many runners are configured.
- **Dollar budget remaining** for this sprint cycle.
- **Human review bandwidth** -- a per-cycle review-hours number set
  by the operator. Captures the iron rule from the research: an
  AI workforce that out-produces its reviewers ships garbage.

When you allocate cards to a sprint, a stoplight indicator goes
green / yellow / red as the cumulative cost crosses the cheapest of
those three. Token-budget-as-capacity is the new capacity planning
(per the sprint-planning research) and your stack uniquely supports
the dollar version, not just the token version.

**Effort: M.** **Depends on:** 2.1, 1.3.

### 2.3 Backlog grooming surface (separate route)

A `/backlog` view distinct from the kanban. Shows backlog cards in
a dense table with inline-editable fields (title, project, points,
stakes, tags), bulk-select, bulk-edit, "promote to sprint N." A
"Ready" toggle (just a label) splits ice-box from ready-for-sprint.
This is the surface where you spend grooming time -- the kanban is
the running-the-work surface.

**Effort: M-L.** **Depends on:** 1.2, 1.4.

### 2.4 Triage inbox (Linear pattern)

A pre-backlog lane. New submit-story batches land here (or any card with
a `triage:` label). Each item shows: proposed title, body excerpt, $
estimate, and a Linear-style "Similar to..." section that flags
near-duplicate existing cards (title similarity is fine for v1; embed
similarity is horizon 3).

One-click actions: promote to backlog (with rank), merge into existing
card, decline. This is where the agentic-AI volume problem gets managed.

**Effort: M.** **Depends on:** 1.2, 1.4. Builds on existing
submit-story dry-run.

### 2.5 Per-card live event timeline (in card detail)

SSE events are already published end-to-end. In the card detail modal,
add a collapsible timeline that shows the event stream for this card:
`claimed`, `started`, `heartbeat` (collapsed by default), `cascade`,
`verifier_called`, `finished`, `merged`. This is observability for the
operator, without standing up a separate Gantt or "logs" surface.

**Effort: M.** **Depends on:** small backend addition to persist
event history per card (it's only published live today).

### 2.6 Dependency view

A view (or a modal subpanel) that renders the `depends_on` DAG of the
current filtered card set. Each node clickable to the card. Cycles
detected and flagged. Used for: "what's blocking this sprint?", "what
can we parallelize?", "which card unblocks the most?".

**Effort: M.** **Depends on:** 1.2 (the filter set decides what's
in the graph).

### 2.7 WIP limits per column, with agent-aware defaults

Soft warning when Active > N. Default N = number of configured
parallel runners. Reframes "WIP limits" from "guideline most teams
ignore" to "concrete cap matching real concurrency."

**Effort: S.** **Depends on:** runner config visibility.

### 2.8 Multi-select bulk actions

Shift-click or `X`-to-select multiple cards in any view (backlog,
kanban, triage). Bulk actions: move to sprint, set rank, set label,
re-tier, cancel. Linear's keyboard-first multi-select is the model
to follow, not Jira's checkboxes.

**Effort: M.** **Depends on:** 1.6.

### 2.9 Burndown + velocity per sprint

Two small charts on a sprint detail page: $ burndown (planned vs
actual, day by day) and points-velocity over the last 5 cycles. Not
the homepage -- a sprint detail view. The research is clear: people
glance at these in retro, they shouldn't dominate the daily UI.

**Effort: M.** **Depends on:** 2.1.

### 2.10 Retros UI

Backend exists. Pull cards completed in the last cycle and surface
the deltas: estimated vs actual tokens, estimated vs actual minutes,
estimated vs actual dollars. Highlight outliers. Free-text retro
notes saved per cycle. This is your feedback loop on the estimation
model.

**Effort: M.** **Depends on:** 1.3, 2.1.

### 2.11 Per-project grouping / lens

Cards carry `project:`. Add a "group by project" toggle to the kanban
and a project-filter chip (already covered in 1.2 but the lens makes
each project look like its own board).

**Effort: S.** **Depends on:** 1.2.

### Mid-term summary

After horizon 2, you can plan a sprint, allocate work to fit capacity,
groom the backlog, triage incoming agent-generated cards, watch the
work execute live, run a retro, and tune estimates. The board has
become the cockpit.

---

## 5. Horizon 3: Long-term (goals to strive toward)

**Definition.** The agentic-AI-maximizing surface. Where one developer
operates like a team of five because the dashboard composes with the
runner instead of just watching it. Effort estimates here are
intentionally rougher -- each item is its own design exercise.

### 3.1 Cost-cap governor + UI

Today `cost_cap_usd` exists in the frontmatter but no backend enforces
it. Build:

- **Runner-side**: when an executor's cumulative cost on a card crosses
  `cost_cap_usd`, the governor pauses the run and emits a
  `card-cap-hit` event.
- **Dashboard**: a paused card shows up in a "needs cap decision"
  inbox. Operator options: bump the cap (with a reason logged),
  cancel the run, hand off to a cheaper tier (Opus -> Sonnet).
- **Sprint-level cap**: similar logic but at the sprint dollar
  budget. Crossing it pauses all sprint-tagged runners.

This is the single most important horizon-3 feature for "small team
operating like a large one" -- it lets you queue ambitious work
without surprise bills.

**Effort: L.** **Depends on:** 1.3 (cost surface), runner cooperation.

### 3.2 Pre-flight cost estimation per card

When a card is about to dispatch, compute a predicted cost from:

- Historical median for similar cards (same tier, similar `touches`
  pattern, similar token count on submission).
- The configured model's rate.
- An optional Bayesian adjustment if the card has previously been
  attempted.

Show "this attempt is budgeted $X; last 10 cards of this tier ran
$Y &plusmn; $Z" in the card detail and on the dispatch confirmation.
This is what turns the cost-cap from "hard stop" into "informed
choice."

**Effort: M-L.** **Depends on:** 3.1, accumulated history (so this
gets better over time; useful from day one even with thin data).

### 3.3 Retry-budget UI

Cap both `max_attempts` and `total_$_per_card`. Visualize the **retry
tax** -- the research shows a 20% per-step failure rate on a 5-step
agent burns 2.2-2.5x the tokens of a single clean run, because retries
replay the full context. Surface that math on the card detail and on
the runner-config screen so operators see why retries are expensive.

**Effort: M.** **Depends on:** 3.1.

### 3.4 Parent-card with attempt children (sub-issue model)

Multiple runs on one card become sub-issues under a parent. Cost rolls
up at the parent level. The board shows the parent; clicking expands
to attempts (with their status, cost, diff). This solves the
multi-attempt problem cleanly and matches the Devin/Cognition
data model.

**Effort: L.** **Depends on:** schema work (parent_card_id on the
attempt), Triage to handle it.

### 3.5 Agent fleet view (`/agents` route)

A dedicated route showing every configured runner as a row:

- Current card (with $/min spend rate ticking up live).
- Model.
- Last heartbeat (red if stale).
- Total $ spent today, this sprint.
- Quick actions: pause, drain (finish current, claim no more),
  resume.

Plus a sparkline of concurrent active runners over the past 24h.
This is the cockpit's "engineroom" view -- it's how you know whether
to dispatch more work or let things settle.

**Effort: L.** **Depends on:** runner emits a heartbeat with
cost-so-far (likely a small runner-side change).

### 3.6 Smart triage with semantic dedup

Horizon 2 had simple title-similarity dedup; horizon 3 uses embeddings.
At intake, embed the proposed card's title + body, search nearest
neighbors in the existing card store, and surface ranked similar
cards with similarity scores. Threshold-flag candidate duplicates for
merge. This becomes load-bearing once the runner is generating dozens
of cards a day.

**Effort: M-L.** **Depends on:** 2.4 (triage inbox), an embedding
provider (could be local or API).

### 3.7 Confidence-based escalation policy editor

Today the runner has a cascade-on-confidence routing table (see
`b042-05-document-cascade-routing`). Surface it as a visual editor in
the dashboard: from-tier, to-tier, threshold, "escalate to human at
&lt;X% confidence." Operator can tune the 10-15% review rate the
research recommends without editing config files.

**Effort: M.** **Depends on:** runner reads the table dynamically
(may already; verify).

### 3.8 Cost anomaly alerting

If a card's live spend exceeds 2x its pre-flight estimate at any
point during execution, alert and offer a kill-switch with one click.
Same pattern for "runaway execution" (no progress events for N
minutes) -- the research calls runaway execution out as one of the
top still-unsolved failure modes.

**Effort: M.** **Depends on:** 3.2, 3.5.

### 3.9 Predictive sprint planner

Given the current backlog, capacity constraints, and goal labels,
propose 1-3 candidate sprint plans that maximize value-per-dollar
or de-risk a milestone. Human picks one or edits. This is where
auto-decomposition (already partial via `/submit`) extends to
auto-allocation.

Stays a proposal until accepted -- never auto-commits. Devin's
"Interactive Planning" model is the right one: agent drafts,
human edits, human runs.

**Effort: L.** **Depends on:** 2.1, 2.2, 2.9 (velocity data),
some kind of scoring model.

### 3.10 Sprint-goal alignment scoring

When a card is added to a sprint, compute semantic similarity to
the sprint goal text and surface a score on the card chip. Reds for
"weak fit" prompt the operator to either reword the goal, defer the
card, or accept the drift. Cheap, high-signal sprint hygiene.

**Effort: S-M.** **Depends on:** embeddings (shared with 3.6), 2.1.

### 3.11 Replay / branch agent runs

From a failed or in-progress run, fork: clone the card, modify the
prompt or context, dispatch the fork as a new attempt. The original
sticks around for comparison. The diff between attempts becomes
reviewable.

**Effort: L.** **Depends on:** 3.4 (attempt model), runner cooperation
(checkpoint support is a stretch goal; "rerun from prompt" without
checkpoint is enough for v1).

### 3.12 Diff-view in the In Review column

The `awaiting_amendment_review` column today renders a card tile. It
should render the actual proposed diff inline (or in a side panel) so
review happens in the dashboard, not in `git`. Plus the verifier's
comments and the human's accept/reject/amend actions. This is the
single feature most likely to move review time from "hours" to
"minutes" and is the gating constraint on parallel-agent throughput
per the capacity research.

**Effort: L.** **Depends on:** runner emits diff with the
"needs_review" event.

### 3.13 Auto-decomposition into a multi-sprint plan

Today submit-story decomposes a story into a flat set of cards. Extend
it to also propose a multi-sprint **plan**: which cards in sprint
+0, which in sprint +1, what the goal of each is. The operator
reviews the plan as a whole before any card commits. This is the most
ambitious horizon-3 item and the most "small team feels like a large
team" leverage.

Keep the current dry-run-then-approve loop -- never auto-commit a
plan.

**Effort: XL.** **Depends on:** 2.1, 2.2, 3.6, 3.9.

### Long-term summary

After horizon 3, the dashboard isn't a viewer of an agent system. It's
the planning and governance layer that makes the agent system safe to
turn up. The leverage scales with how aggressively you dispatch work,
because the safeguards (cost-cap, anomaly, review, retry-budget) all
scale with you.

---

## 6. Forks for Drew to decide

Where the research surfaced a genuine product fork, I'm laying out the
options rather than picking. My recommendation is on each, but these are
the calls you should make explicitly.

### Fork A: where does manual rank live

- **Option A1**: in card frontmatter (`rank:` field on every card).
  Pro: self-describing on disk, survives if the dashboard DB dies.
  Con: every drag-reorder rewrites the file, the watcher fires, the
  card timestamp changes, git diffs churn.
- **Option A2**: in SQLite, keyed by card id. Pro: no file churn,
  fast. Con: not visible from outside the dashboard, lost if DB blows
  away (recoverable from `mtime`-based default).

**Recommendation: A2.** Keep the disk file as the work definition; the
dashboard owns the rank as a presentation concern.

### Fork B: card identifier scheme

- **Option B1**: keep the current `<batch>-<NN>-<slug>` ids (e.g.
  `b042-01-runner-claim-loop`). Pro: descriptive, no migration.
  Con: long, paste-unfriendly, doesn't sort intuitively across
  batches.
- **Option B2**: add a short Linear-style code (`AC-123`) alongside,
  computed by the backend at card-creation time. Pro: copy-pasteable
  into branches/PRs/Slack. Con: two ids per card.

**Recommendation: B1 for now**, but **add `cardShortId` already does
half of this** -- display `b042-01` on tiles and click-to-copy that.
If the volume of cards grows past a few hundred and the ids stop being
readable, revisit B2.

### Fork C: filter UX

- **Option C1**: chip-builder (Linear, Height, GitHub Projects).
- **Option C2**: query DSL (Jira's JQL).

**Recommendation: C1**, no debate. Even Atlassian is walking back JQL.

### Fork D: cost-cap enforcement

- **Option D1**: hard stop. Cross the cap, runner dies, card lands in
  blocked.
- **Option D2**: soft stop with confirm. Cross the cap, runner pauses,
  operator approves a bump or kills the run.
- **Option D3**: predictive-only -- never stop, just alert.

**Recommendation: D2.** The cap is a guardrail, not a wall. Most
overruns are legitimate (a card was harder than estimated) and a
soft-stop lets the operator decide quickly. D1 is too brittle; D3
defeats the point of having a cap.

### Fork E: sprint cadence

- **Option E1**: Linear-style fixed cycles (every 1 or 2 weeks,
  auto-rollover spillover).
- **Option E2**: Jira-style explicit sprints (you create one, fill it,
  start it, end it).

**Recommendation: E1.** Fixed cadence suits an autonomous runner that
never pauses. Auto-rollover is universally loved by Linear users.
Manual sprint ceremony adds friction the AI workforce doesn't need.

### Fork F: where the dollar cost is computed

- **Option F1**: runner emits `cost_usd:` in the card frontmatter on
  finish. Pro: simple. Con: changes to rates don't recompute history;
  in-flight cost is hidden.
- **Option F2**: dashboard backend holds a rate table (per model,
  per date) and computes cost on demand from tokens. Pro: rate
  changes recompute history, in-flight cost is computable, single
  source of truth. Con: rate table maintenance.

**Recommendation: F2.** Rates change. History should re-price.
Maintenance is a tiny YAML file.

### Fork G: auto-decomposition autonomy level

- **Option G1**: current. Submit story -> planner drafts -> human
  reviews dry-run -> human approves -> cards commit. (One review.)
- **Option G2**: trusted-template bypass. For a small set of
  pre-approved goal templates, skip the dry-run and land cards in
  Triage instead of Backlog.
- **Option G3**: fully autonomous, cards land directly in Backlog.

**Recommendation: G1 by default, G2 for trusted templates once
templates exist.** G3 is the trap that produces the "agents
out-producing review bandwidth" failure mode.

### Fork H: agent / runner as first-class entity

- **Option H1**: `claimed_by` stays a free-form string on the card.
  Pro: nothing to build. Con: no per-agent view, no fleet
  observability, no avatar/history.
- **Option H2**: agents are a first-class entity in SQLite (id,
  display name, model, configured-by, parallelism limit, $-spent,
  current card). The card's `claimed_by` becomes a foreign key.

**Recommendation: H2** when you start horizon 3.5 (the agent fleet
view). H1 is fine for horizons 1-2.

---

## 7. Skip list (things that look standard but aren't worth it here)

From the research, things to **not** build:

- **WSJF / scoring frameworks** for backlog prioritization. Manual rank
  is what teams actually use.
- **Unlimited custom fields.** Jira's own data shows custom-field bloat
  tanks performance linearly; curate.
- **3+ levels of parent/child nesting.** One level (parent -> attempts)
  is enough.
- **Per-person capacity widgets** in their classic form. Built, mostly
  unused. The agent-capacity version (2.2) is different -- it's
  measuring constraints that actually bind.
- **Custom workflow per issue type.** Maintenance nightmare.
- **Burndown as the homepage.** Glance-at-it-in-retro, not the daily
  view.
- **Trello-style power-up sprawl.** Either build it in or live without
  it; don't stitch.
- **Status workflows with 20 states.** Cap at 5-7. You're at 5 already.
- **Per-agent capacity dashboards before there are 5+ agents.** Premature.

---

## 8. Feature catalog (everything in one table)

Effort scale:
- **S**: 1-3 days
- **M**: 1-2 weeks
- **L**: 3-5 weeks
- **XL**: 6+ weeks (real design phase needed)

| # | Feature | Horizon | Effort | Depends on | One-line |
|---|---------|---------|--------|------------|----------|
| 1.1 | Command palette (Cmd-K + fuzzy search) | Near | M | -- | Linear-style palette: navigation, commands, card search, all keyboard |
| 1.2 | Chip-based filter bar | Near | M | -- | Project / batch / runner / tier / stakes / pin / merge-status chips above board |
| 1.3 | Per-card dollar-cost chip | Near | M | -- | $est on backlog, $spent on active/done; color-step toward `cost_cap_usd` |
| 1.4 | Manual rank within column, drag-to-reorder | Near | M | (small backend) | Rank stored in SQLite; sort dropdown per column |
| 1.5 | Saved views | Near | M-L | 1.2, 1.4 | Filters + grouping + sort + columns, named, URL-sharable |
| 1.6 | Keyboard parity for common actions | Near | S | 1.1, 1.2, 1.4 | `S` status, `/` search, `F` filter, `X` select, `Shift-S` sprint |
| 1.7 | Tile polish: copy-id, age, dependency badge | Near | S | -- | Click-to-copy short id; "2h ago"; "blocked on N" badge |
| 1.8 | Column header cost rollup | Near | S | 1.3 | `$ est` / `$ spent` alongside count badge |
| 2.1 | Sprint planner UI (the real one) | Mid | L | 1.4 | Backend exists; build timeline with budget meters, auto-rollover |
| 2.2 | Capacity model: min(agents, $, review) | Mid | M | 2.1, 1.3 | Stoplight on sprint header for the binding constraint |
| 2.3 | Backlog grooming surface | Mid | M-L | 1.2, 1.4 | `/backlog` route, dense table, bulk-edit, "Ready" toggle |
| 2.4 | Triage inbox | Mid | M | 1.2, 1.4 | Pre-backlog lane; title-similarity dedup; promote/merge/decline |
| 2.5 | Per-card live event timeline | Mid | M | (small backend) | Collapsible stream of `claimed`, `heartbeat`, `verifier_called`, etc. |
| 2.6 | Dependency view (DAG) | Mid | M | 1.2 | Graph of `depends_on` for the current filter set; cycle detection |
| 2.7 | WIP limits per column, agent-aware | Mid | S | runner config | Default cap = parallel runner count |
| 2.8 | Multi-select bulk actions | Mid | M | 1.6 | Linear-style keyboard-first multi-select |
| 2.9 | Burndown + velocity per sprint | Mid | M | 2.1 | Sprint detail page, not homepage |
| 2.10 | Retros UI | Mid | M | 1.3, 2.1 | Pull last cycle; surface estimate-vs-actual deltas; free-text notes |
| 2.11 | Per-project lens / grouping | Mid | S | 1.2 | Group-by-project toggle, project chip already in 1.2 |
| 3.1 | Cost-cap governor + UI | Long | L | 1.3, runner | Soft-stop with operator-confirm; sprint-level cap likewise |
| 3.2 | Pre-flight cost estimation | Long | M-L | 3.1, history | "Budgeted $X; last 10 cards of this tier ran $Y &plusmn; $Z" |
| 3.3 | Retry-budget UI | Long | M | 3.1 | Cap attempts AND $; visualize the 2-2.5x retry tax |
| 3.4 | Parent-card with attempt children | Long | L | schema | Sub-issue model; cost rolls up to parent |
| 3.5 | Agent fleet view (`/agents`) | Long | L | runner heartbeat | Per-runner row; live $/min; pause/drain/resume |
| 3.6 | Smart triage with semantic dedup | Long | M-L | 2.4, embeddings | Embed-based nearest-neighbor candidate-duplicate detection |
| 3.7 | Confidence-based escalation editor | Long | M | runner | Visual editor for the cascade routing table |
| 3.8 | Cost anomaly alerting | Long | M | 3.2, 3.5 | Spend &gt; 2x estimate -> alert + kill-switch |
| 3.9 | Predictive sprint planner | Long | L | 2.1, 2.2, 2.9 | Agent proposes 1-3 candidate plans; human picks and edits |
| 3.10 | Sprint-goal alignment scoring | Long | S-M | embeddings, 2.1 | Semantic-fit score on each card vs sprint goal |
| 3.11 | Replay / branch agent runs | Long | L | 3.4, runner | Fork a card, modify prompt, re-dispatch as a new attempt |
| 3.12 | Diff-view in In Review column | Long | L | runner emits diff | Review happens in dashboard, not in `git` |
| 3.13 | Auto-decomposition into multi-sprint plan | Long | XL | 2.1, 2.2, 3.6, 3.9 | Submit a goal, get a multi-sprint plan to review |

---

## 9. Suggested first-quarter cut

If you want a concrete "do this in the next 6-8 weeks" cut, this is what
I'd take from the table, in order. Most are near-term, with two
mid-term items pulled forward because they unlock disproportionate
leverage.

1. **1.3** (dollar-cost chip) -- the differentiator and the prerequisite
   for cost-cap.
2. **1.1** (Cmd-K) -- highest perceived sophistication per build hour.
3. **1.2** (filter chips).
4. **1.7** (tile polish) -- small, high impact.
5. **1.8** (column cost rollup).
6. **1.4** (manual rank).
7. **1.5** (saved views).
8. **1.6** (keyboard parity).
9. **2.1** (sprint planner UI) -- the second-biggest payoff after cost.
10. **2.2** (capacity model) -- only useful with 2.1 done.

That leaves the heavier mid-term items (grooming, triage, dependency
view, retros) and all of horizon 3 for the following quarter, when
volume of agent-generated cards has grown enough to need them.

---

## 10. Open questions for Drew

These are worth deciding before the first horizon-3 card is scoped, but
not blockers for horizon 1:

1. **What's the right cost-cap default?** Per card, per sprint, per
   day? Suggested: per card (set on submit or inherited from project
   default) + per sprint (rolling cap).
2. **What's the human review-bandwidth number** for capacity math?
   Hours per cycle? Reviews per cycle? Or just "cards we're willing to
   merge per cycle"?
3. **Do agents get user-like avatars** (H2), and if so, are they
   per-runner-id (one avatar per process) or per-model (one for
   Sonnet, one for Opus)?
4. **Embeddings**: local model (faster, no API) or API (better
   quality)? Affects 3.6 / 3.10 / 3.13.
5. **What's the multi-tenant story** for the dashboard? The blocked
   card `b044-03-multi-tenant-tokens` exists; should this roadmap
   assume single-tenant for now, or design for multi-tenant from
   horizon 2 onward?

---

## 11. Notes on what this document is and isn't

- It's a **proposal**, not a commitment. I picked recommendations where
  the research was clear and surfaced forks where the research showed
  real disagreement.
- It's grounded in the **current code**, not a wish list. Every "what
  exists" claim was verified against `Nexus/jovial-einstein-4b9732`.
- It's **deliberately short on UX mockups**. Once a horizon-1 feature
  is greenlit, the next step is a per-feature design (the
  `superpowers:brainstorming` flow is a good fit for that).
- It assumes you keep the **current architectural choices**:
  filesystem-of-record, SSE for live updates, SQLite for
  dashboard-only state, bearer-token auth, the submit-story dry-run
  loop. Each of those could be revisited, but none of the features
  above require it.

---

End of roadmap.
