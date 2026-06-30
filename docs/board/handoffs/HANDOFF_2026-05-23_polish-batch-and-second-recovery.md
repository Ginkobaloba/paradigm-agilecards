# Polish-batch + Second Recovery Handoff (2026-05-23)

Session-opening state per dispatch: "Drew is reviewing and merging
#12-#15 right now. Build more dashboard polish on top of PR #15, rebase
onto main once they land." Actual state at session start: PR #12 landed
on main; PRs #13, #14, #15 marked MERGED on GitHub but their commits
never reached main (same stacked-PR-base strand pattern as 2026-05-22).

This session did two things:

1. Built three logically-grouped horizon-2 polish PRs on top of PR #15's
   tip: 2.11 per-project lens, 2.7 WIP limits, 2.6 dep DAG modal.
2. Recovered the second-pass strand by re-cherry-picking the missing
   feature commits (rank, cmdk, timeline+handoff) onto fresh branches
   off current main, then rebasing the three new feature PRs to chain
   on top.

End state is one connected six-PR stack, all open, awaiting Drew's
review and merge. Final test totals: backend 57 pass, frontend 89 pass,
build clean.

## Next Session Onboarding

Before doing anything else, the next session must:

1. Read `C:\dev\SESSION_PROTOCOL.md`.
2. Read this project's `CLAUDE.md` if present.
3. Read this handoff doc end-to-end.
4. Run `vstart` (or `C:\dev\_scripts\session-start.ps1`).

## Open PR stack at session end

Merge in numerical order: 19 → 20 → 21 → 16 → 17 → 18. **Critical:** see
"How to avoid the strand happening a third time" below before merging.

| PR | Branch | Base | Title | Roadmap items |
|----|--------|------|-------|---------------|
| [#19](https://github.com/Ginkobaloba/agile-cards-board/pull/19) | `feature/manual-rank-v3` | `main` | feat(rank): drag-to-reorder within a column + sort dropdown (re-land of #13) | 1.4 |
| [#20](https://github.com/Ginkobaloba/agile-cards-board/pull/20) | `feature/cmdk-filter-views-v3` | `feature/manual-rank-v3` | feat(nav): Cmd-K palette, filter chips, saved views, keyboard (re-land of #14) | 1.1, 1.2, 1.5, 1.6 |
| [#21](https://github.com/Ginkobaloba/agile-cards-board/pull/21) | `feature/card-event-timeline-v2` | `feature/cmdk-filter-views-v3` | feat(timeline): per-card lifecycle event stream in card detail (re-land of #15) | 2.5 |
| [#16](https://github.com/Ginkobaloba/agile-cards-board/pull/16) | `feature/project-lens` | `feature/card-event-timeline-v2` | feat(lens): group-by-project lens on the kanban | **2.11 NEW** |
| [#17](https://github.com/Ginkobaloba/agile-cards-board/pull/17) | `feature/wip-limits` | `feature/project-lens` | feat(wip): soft per-column WIP limits with inline editor | **2.7 NEW** |
| [#18](https://github.com/Ginkobaloba/agile-cards-board/pull/18) | `feature/dep-view` | `feature/wip-limits` | feat(deps): dependency DAG modal for the visible card set | **2.6 NEW** |

Untouched per instruction: [#1](https://github.com/Ginkobaloba/agile-cards-board/pull/1) (Dependabot Vite major bump).

## How to avoid the strand happening a third time

GitHub's "Merge pull request" button on a stacked PR (base = some other
feature branch, not main) merges INTO the base branch, not into `main`.
It flips the PR to MERGED state regardless, because the head SHA now
appears in the base branch's history. But that history doesn't reach
`main`, so the work doesn't actually ship.

The auto-retarget that fixes this only fires when the just-merged PR's
head branch gets DELETED from origin. If you keep the branch after
merge, GitHub leaves the upstream PR's base alone, and clicking
"Merge" on it merges into the now-stale stack base again.

Two reliable fixes (pick one or both):

1. **Enable repo-level "Automatically delete head branches"** at
   Settings -> General -> Pull Requests. After #19 merges to `main`,
   GitHub deletes `feature/manual-rank-v3`, which triggers #20's base
   to auto-retarget to `main`. Then merging #20 actually goes to
   `main`. Cascade continues for #21, etc.
2. **Switch the merge strategy to "Squash and merge"** for stacked
   PRs. The squash commit lands on `main` directly. Auto-retarget for
   upstream PRs still depends on head-branch deletion, so this works
   best in combination with (1).

I'd enable (1) right now in the repo settings before merging this
stack, even if (1) is the only change. It's the smallest, least
intrusive setting that prevents the strand entirely.

## What's new in this session (PRs #16, #17, #18)

### PR #16 -- 2.11 per-project lens

A new "group" dropdown on the right rail of the filter bar reshapes
each column: cards partition by their `project` frontmatter field,
with subheaders showing project name and per-project count. Cards with
no project land in an "Unassigned" bucket at the bottom. State
persists to localStorage; drag-drop and sort behavior unchanged.

**Files:** `state/lens.ts`, `state/lens.test.ts` (7 tests),
`components/Column.tsx` (gain `groupBy` prop + group rendering),
`components/FilterBar.tsx` (group toggle), `routes/Kanban.tsx`
(thread lens state).

### PR #17 -- 2.7 WIP limits per column with soft warn

Each column has a default soft limit (Active=3, In Review=5; others
unlimited). When the visible card count exceeds the limit, the count
pill flips to warn-tinted with an "over" suffix. At-cap is
accent-tinted as a "you are here" cue. Clicking the pill opens a
small inline number input with Set / Reset / cancel. localStorage
override per column.

The roadmap (2.7) ties the default to "number of configured parallel
runners." That signal doesn't exist yet, so v1 hardcodes a sensible
default and offers an override. When runner-config visibility lands,
the default constructor swaps from `DEFAULT_LIMITS[status]` to
whatever the runner reports; the override path stays unchanged.

**Files:** `state/wipLimits.ts`, `state/wipLimits.test.ts` (8 tests),
`components/Column.tsx` (new `CountPill` component replaces the
static count badge).

### PR #18 -- 2.6 dependency view (DAG modal)

A new "Deps..." button on the filter bar opens a Radix dialog that
renders the `depends_on` DAG of the currently visible (post-filter)
card set. Cards lay out as columns by depth: depth 0 ("Ready") on the
left, downstream chains to the right. Cycles flagged in warn color via
iterative Tarjan SCC. Each cell shows id, title, status dot, count of
visible vs. external deps, and "unblocks N" (transitive dependents).
Click any cell to open that card's detail modal; the dep view stays
open underneath so the operator can flip through several cards.

**Files:** `lib/depGraph.ts`, `lib/depGraph.test.ts` (12 tests),
`components/DependencyView.tsx`, `components/FilterBar.tsx`
(Deps... button), `routes/Kanban.tsx` (own dep-view state + visible-cards
flatten).

## Decisions

### Per-project lens vs. saved views vs. filter chips

The filter bar already has a project chip (PR #14). The lens is a
distinct concern: filtering narrows what's shown; lens reshapes how
it's shown. A saved view (PR #14) describes filters + sort + columns
but not the render shape. Keeping the three separate means one saved
view can be viewed flat or grouped without producing duplicate view
definitions. If groupBy earns its way into shareable URLs later, it
folds into the view payload in one field change.

### WIP limit overrides live in localStorage, not the backend

The default WIP limit constants live in the frontend (a constant
object keyed by StatusId), and overrides persist to localStorage. The
roadmap ties defaults to runner concurrency; when the runner config
becomes visible, the default source can swap. There's no backend
table for this yet because the data is purely a UI concern -- the
disk + ranks already say what's in each column; the limit just says
when to render an alert.

### DAG modal uses depth columns, not a force-directed render

The roadmap calls for a DAG view that answers "what's blocking?",
"what can parallelize?", "which card unblocks the most?". Depth
columns answer all three without an SVG layout library. Adding
dagre / elk would mean a heavy dep for a secondary surface. The
column form also keeps cycles visually obvious -- a cycle node
appears at whatever depth its non-cycle deps push it to, with a
warn-tinted border. A real force-directed render would tangle them.

### Visible-set-relative depth

"Depth 0 = no upstream deps inside the visible set" is what the dep
view ships. The same card could be Ready in a project-filtered view
but Not-Ready in an unfiltered view. This is intentional -- the
view's job is to answer "given what we're looking at right now,
what can the runner start?", not "globally, what is universally
ready?". External deps still show as "ext N" on the cell so the
operator can spot when a card is gated by something out of view.

## Verification

From `feature/dep-view` (stack tip after rebase):

- `npm --prefix backend run typecheck` -- clean
- `npm --prefix backend test` -- 57 pass, 8 suites
- `npm --prefix frontend run typecheck` -- clean
- `npm --prefix frontend test` -- 89 pass, 10 suites (27 new across the
  three new PRs: 7 lens + 8 wip-limits + 12 depGraph)
- `npm --prefix frontend run build` -- clean

Recovery branches were not re-verified from scratch (they are
straight cherry-picks of commits that already pass), but the rebased
chain compiles + tests cleanly from the top.

## Risks for Drew's review

- **PR #18 manual smoke needed.** I have no live runner, so the DAG
  modal was not exercised against real interconnected cards. Worth a
  3-minute manual: seed a few cards with synthetic `depends_on`
  chains, click "Deps...", confirm the depth columns render and a
  click-through to the card detail works.
- **PR #17 default of 3 on Active.** If you regularly run 4+ active
  cards, the column will warn-pill on every load until you click the
  pill and set it higher. The override persists in localStorage so
  you only do this once per device.
- **PR #16 grouping with rank-aware sort.** Rank sort within a project
  group should "just work" because partitioning preserves input order
  and the input is already rank-sorted upstream. But I didn't write
  an integration test for the rank-sort-x-group interaction; spot-check
  it by setting sort=Rank, group=Project, dragging two cards in the
  same project group, reload, confirm the order persists.
- **The strand pattern.** See "How to avoid this happening a third
  time" above. Strongly recommend enabling repo-level
  "Automatically delete head branches" before merging this stack.

## What's next from the roadmap

After this stack, horizon 2 is mostly cleared except for the bigger
items. Section 9 of `docs/DASHBOARD_ROADMAP.md` priorities still
unbuilt:

1. **2.1 sprint planner UI (L)** -- biggest payoff. Backend speaks
   `/api/sprints`. Probably 2-3 PRs of its own.
2. **2.2 capacity model (M)** -- depends on 2.1.
3. **2.3 backlog grooming surface (M-L)** -- `/backlog` route with
   bulk-edit table.
4. **2.4 triage inbox (M)** -- pre-backlog lane for new
   submit-story batches.
5. **2.5 per-card live event timeline** -- DONE (PR #21).
6. **2.6 dependency view** -- DONE (PR #18).
7. **2.7 WIP limits** -- DONE (PR #17).
8. **2.8 multi-select bulk actions (M)** -- pairs well with 2.3.
9. **2.9 burndown + velocity (M)** -- depends on 2.1.
10. **2.10 retros UI (M)** -- depends on 2.1.
11. **2.11 per-project lens** -- DONE (PR #16).

The natural next session is **2.1 sprint planner UI**: longest path
to the cockpit value the dashboard is reaching for. It's L effort,
so probably 2-3 sessions or 2-3 stacked PRs.

## Pointers

- Roadmap: `docs/DASHBOARD_ROADMAP.md`
- Prior handoffs:
  - `docs/handoffs/HANDOFF_2026-05-17_dashboard-v0plus.md`
  - `docs/handoffs/HANDOFF_2026-05-18_submit-story.md`
  - `docs/handoffs/HANDOFF_2026-05-22_recovery-and-timeline.md`
    (first recovery + timeline)
- This handoff documents both the second recovery and the polish-batch
  feature work. If we land in the same strand state next session, the
  recovery recipe is in `HANDOFF_2026-05-22` -- cherry-pick the
  feature commits onto fresh branches off main, rebase any in-flight
  work on top. But please enable auto-delete-head-branches first.
