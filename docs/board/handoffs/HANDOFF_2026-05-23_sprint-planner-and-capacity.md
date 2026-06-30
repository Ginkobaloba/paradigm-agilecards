# Sprint Planner + Capacity Handoff (2026-05-23, later session)

Session goal per dispatch: continue agile-cards-board with the next
roadmap features on top of PR #18's tip. Drew authorized a third build
round after merging the previous polish stack (#16-#21).

This session built **roadmap item 2.1 (sprint planner UI)** as PRs
A + B and **roadmap item 2.2 (capacity model with stoplight)** as PR C,
all stacked. The sprint planner v0+ placeholder is now a real list +
detail surface with member assignment, edit-in-place metadata, and a
three-constraint capacity panel (points / $ / review hours).

Final state at session end: 3 new PRs open, all stacked, all green.
Backend 66 pass; frontend 97 pass; builds clean.

## Next Session Onboarding

1. Read `C:\dev\SESSION_PROTOCOL.md`.
2. Read this project's `CLAUDE.md` if present.
3. Read this handoff doc end-to-end.
4. Read the preceding handoff (`HANDOFF_2026-05-23_polish-batch-and-second-recovery.md`)
   if you haven't -- it documents the strand pattern that's relevant
   to the residual-strand note below.
5. Run `vstart`.

## Open PR stack at session end

| PR | Branch | Base | Title | Roadmap |
|----|--------|------|-------|---------|
| [#22](https://github.com/Ginkobaloba/agile-cards-board/pull/22) | `feature/sprint-backend-list` | `feature/dep-view` | feat(sprints): backend extensions + sprint list view | **2.1a NEW** |
| [#24](https://github.com/Ginkobaloba/agile-cards-board/pull/24) | `feature/sprint-detail` | `feature/sprint-backend-list` | feat(sprints): sprint detail page with member list + backlog picker | **2.1b NEW** |
| [#25](https://github.com/Ginkobaloba/agile-cards-board/pull/25) | `feature/sprint-capacity` | `feature/sprint-detail` | feat(sprints): capacity meters with stoplight (2.2) | **2.2 NEW** |

Other open PRs not from this session:
- [#1](https://github.com/Ginkobaloba/agile-cards-board/pull/1) Dependabot Vite major (untouched per long-standing instruction).
- [#23](https://github.com/Ginkobaloba/agile-cards-board/pull/23) `security/scrub-cloudflare-ids-...` -- Drew's separate task.

## ⚠ Residual strand on PRs #16-#18 and #20-#21

GitHub reports these merged at 21:28-21:29 UTC today. Verified against
`origin/main`: **only PR #19 (rank, re-land of #13) actually has its
content on main**. The other five PRs are marked merged but their
feature commits are not in `main`'s reachable history:

```
foreach $sha in (38605ab cmdk, 9231067 timeline, f28b276 lens,
                 e1b97f2 wip-limits, b12854a deps):
  git branch -r --contains $sha 2>$null | Select-String origin/main
  -> all five: NO MATCH
```

Same stacked-PR-merge pattern as the two prior occurrences -- merging
an upstream PR (base = another stack branch) sends the merge commit
into the base branch, not into `main`. GitHub flips the PR to MERGED
because the head SHA appears in its base branch's history. The
`Automatically delete head branches` setting that the dispatch context
said "is being enabled right now by a separate task" is the right fix,
but it cascades through the stack one merge at a time -- merging six
PRs within ~50 seconds (Drew did 21:28:28 → 21:29:17) outruns the
webhook + auto-retarget round-trip, so only the bottom of the stack
makes it.

**I intentionally did NOT recover this round.** Drew said a separate
task is enabling the setting; that task or a follow-up should also
recover the stranded PRs, and re-doing a third cherry-pick stack here
would just duplicate that work. My new PRs (#22 / #24 / #25) are
stacked on top of the stranded chain via the stranded branches'
tips, so:

- If the residual strand is recovered by re-opening PRs against `main`
  for the missing five (the recipe is in
  `HANDOFF_2026-05-22_recovery-and-timeline.md`), my new PRs auto-
  retarget naturally as each lands.
- If Drew instead chooses to rebase + re-open my new PRs directly
  against main, they would carry the stranded content into main as
  part of PR #22's diff (which would balloon to ~1700 lines). The
  recovery-then-mine path produces cleaner per-PR reviews.

To prevent this happening a fourth time: enable the setting **and**
slow the merge cadence to "merge → wait for branch deletion → merge
next." The webhook usually completes in under 10 seconds.

## What this session built

### PR #22 -- 2.1a: backend extensions + sprint list view

Backend grew the columns the planner needs:

- `status TEXT NOT NULL DEFAULT 'planning'` -- enum at the route
  layer (planning / active / completed / cancelled). SQLite ALTER
  TABLE doesn't allow CHECK on added columns.
- Three capacity targets: `points_target INTEGER`, `dollar_target
  REAL`, `review_hours_target REAL`. Consumed by PR #25.
- `archived_at TEXT` -- soft-delete marker. GET /sprints hides
  archived by default; `?includeArchived=1` surfaces them.

Wire format flipped to camelCase end-to-end. New routes:

- `PATCH /sprints/:id` -- update any subset of fields. Used by the
  edit dialog and by the archive flow.
- `DELETE /sprints/:id/cards/:cardId` -- remove a card from a sprint.

List endpoint returns per-sprint rollups (`cardCount`,
`plannedPointsSum`) via a single GROUP BY join, so the list page
doesn't need a per-sprint fetch.

Frontend replaced the "v1 coming soon" placeholder with a real list
of sprint rows + create-sprint dialog (reusable `SprintFormDialog`).
Rows link to `/sprints/:id`.

9 new node:test route tests with per-test DB cleanup via `beforeEach`.

### PR #24 -- 2.1b: sprint detail page

Two-column layout at `/sprints/:id`:

- **Members panel (left):** each assigned card with status dot, short
  id, title, planned points, remove button. Click opens CardModal.
- **Backlog picker (right):** live-searchable list of backlog cards
  not yet in this sprint; one-click add inherits planned points
  from the card's frontmatter.

Header shows name + dates + goal + status badge with an Edit button
that opens `SprintFormDialog` in edit mode (status field appears).
PR #25 added the capacity panel above this grid.

### PR #25 -- 2.2: capacity meters + stoplight

`computeSprintCapacity(sprint, members, cards, rates)` is pure, in
`lib/sprintCapacity.ts`. Three constraints:

- **points** = sum(plannedPoints || cards[id].points)
- **dollars** = sum(cardCost via the existing rate table)
- **review hours** = (points × REVIEW_MINUTES_PER_POINT) / 60
  (heuristic; constant in the lib, swap to runner-emitted later)

Each metric resolves to `none / ok / warn (≥80%) / over (≥100%)`.
The sprint stoplight is the worst of the three.

`CapacityPanel` renders three horizontal meter bars colored by
level, with a stoplight dot in the header and an "Edit targets"
button that opens the same `SprintFormDialog` (three numeric
inputs surface in edit mode).

8 new vitest cases on the pure compute.

## Decisions

### Stack split into PR A / B / C

Item 2.1 is L (3-5 weeks in the roadmap estimate). Splitting it into
backend+list, then detail+assignment, then capacity gave three
~equally-sized reviewable chunks instead of one mega-PR. Each PR is
internally coherent and ships a working surface (list works without
detail; detail works without capacity meters).

### Wire format flipped to camelCase

The v0+ sprint backend returned snake_case (starts_at, planned_points).
Every other route in this codebase uses camelCase on the wire (PR #10
ranks, PR #11 views, PR #15 events). Flipped sprints to match. SQL
column names stay snake_case; conversion happens at the route layer
via a `shapeSprint` helper.

### Backlog picker is in-component, not a global selector

The picker filters store cards by `status === "backlog"` and the
sprint's member ids. Could be a shared `<CardPicker>` component
later (e.g. for triage 2.4), but a single-call-site v1 keeps the
component focused. When 2.4 or 2.8 (multi-select) lands, extracting
it is straightforward.

### REVIEW_MINUTES_PER_POINT is a single constant

Per-card review minutes isn't a first-class frontmatter field today.
The roadmap (2.2) is explicit that review bandwidth is the iron-rule
constraint: "an AI workforce that out-produces its reviewers ships
garbage." The budget needs to exist even if the per-card estimate
is rough. 5 min/point lives in `lib/sprintCapacity.ts` and can swap
to a runner-emitted estimate later without touching the panel.

### Dollar rollup uses the cost lib's preference

`cardCost` already prefers `actual_tokens` over `estimated_tokens`
when both are present. The sprint dollar sum therefore counts
actual spend on finished cards and estimates on in-flight / backlog
cards. A "spent vs. estimated" split panel could come later if
operators want it.

### No backend sprint_card.plannedPoints editor on the row

Today a member row shows whatever points were stored when the card
was added. `POST /sprints/:id/cards` is upsert-style so re-adding
overrides. Inline editor on the row is a small follow-up; gated on
whether operators actually want per-membership overrides vs. just
using the card's points.

## Verification

From `feature/sprint-capacity` (top of new stack):

- `npm --prefix backend run typecheck` -- clean
- `npm --prefix backend test` -- 66 pass, 9 suites (9 new sprints
  route tests + 57 prior)
- `npm --prefix frontend run typecheck` -- clean
- `npm --prefix frontend test` -- 97 pass, 11 suites (8 new sprint
  capacity tests + 89 prior)
- `npm --prefix frontend run build` -- clean

## Risks for Drew's review

- **Sprint detail page manual smoke needed.** Drag-from-backlog
  wasn't tested with the actual seed data. Worth a 2-minute manual:
  create a sprint, click into it, pick a card, confirm it appears
  in the member list with the right points.
- **Capacity panel against an empty sprint.** Empty sprint should
  show "no targets" until Edit Targets is opened and a number is
  entered. Confirm that "Set targets" CTA toggles to "Edit targets"
  after at least one target is set.
- **The strand.** See ⚠ above. My PRs assume the chain below them
  eventually lands; if Drew rebases instead, PR #22's diff will
  expand significantly.
- **Schema v5 migration on the live DB.** Five `ALTER TABLE ADD
  COLUMN` calls, idempotent via `PRAGMA table_info`. No data motion,
  no defaults backfill needed (existing rows get `status='planning'`
  via the DEFAULT). First boot after merge will run them all.

## What's next from the roadmap

Item 2.1 / 2.2 ship the planner foundation. From section 9 of
`docs/DASHBOARD_ROADMAP.md` "first-quarter cut," still unbuilt:

(near-term horizon now fully clear)

(mid-term horizon -- in priority order):

3. **2.3 backlog grooming surface (M-L)** -- `/backlog` route with
   dense table + bulk-edit. Pairs well with 2.8.
4. **2.4 triage inbox (M)** -- pre-backlog lane for submit-story
   batches. Builds on the existing dry-run loop.
5. **2.8 multi-select bulk actions (M)** -- keyboard-first select,
   bulk move/rank/cancel. Pairs with 2.3.
6. **2.9 burndown + velocity (M)** -- sprint detail charts.
   Depends on 2.1 (now in stack).
7. **2.10 retros UI (M)** -- depends on 2.1. Backend already
   speaks /api/retros.

(long-term horizon -- as previously)

Natural next session: **2.10 retros UI** -- closes out the
sprint-lifecycle loop (plan → run → retro) and is M effort.
Or **2.3 backlog grooming + 2.8 multi-select** as a pair --
they share UI patterns.

## Pointers

- Roadmap: `docs/DASHBOARD_ROADMAP.md`
- Prior handoffs:
  - `docs/handoffs/HANDOFF_2026-05-17_dashboard-v0plus.md`
  - `docs/handoffs/HANDOFF_2026-05-18_submit-story.md`
  - `docs/handoffs/HANDOFF_2026-05-22_recovery-and-timeline.md`
    (first recovery)
  - `docs/handoffs/HANDOFF_2026-05-23_polish-batch-and-second-recovery.md`
    (lens + wip + deps + second recovery)
