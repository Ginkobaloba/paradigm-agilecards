# Orphan Branch Skip Mapping -- 2026-06-18

## What this doc is

Thirteen branches on the archived `agile-cards-board` repo were confirmed SKIP-safe during
a triage audit on 2026-06-18. "SKIP" means their feature payload had already landed on
archive `main` via a parallel set of `recover/*` PRs before the repo was superseded and
grafted into this monorepo as the `apps/board/` subtree (graft commit `2a7a403`).

This doc records the SHA-level archaeology: for each retired branch, the tip SHA, the PR(s)
that delivered equivalent work to archive `main`, and where the same code lives today in
`apps/board/`.

**Full triage methodology and per-orphan dossiers**: `C:\dev\_retros\ORPHAN_BRANCHES_PLAN_2026-06-18.md`

**Triage verdict**: SKIP all 13. No branch has unique content. Every feature is present in
`apps/board/` today.

---

## Context: how the archive works

Archive repo (read-only): `github.com/Ginkobaloba/agile-cards-board`
Archive local clone: `C:\dev\_archive\agile-cards-board-superseded-2026-06-18\`
Monorepo subtree root: `apps/board/` (grafted at `2a7a403`)

The recover/* PRs (#27-#34) were opened on the archive repo in a cleanup pass after the
original feature branches stalled. They re-implemented each feature against a clean main
rather than rebasing the entangled originals. Archive main (tip `04454e8`) was then used
as the subtree source for `apps/board/`.

Do not attempt to rebase or reactivate any of the 13 branches below. The work is already
in the monorepo. If you need the original commit history for attribution purposes, the
archive clone at `C:\dev\_archive\agile-cards-board-superseded-2026-06-18\` is the
read-only reference.

---

## The 13 Retired Branches

### 1. feature/cost-surfaces

| Field | Value |
|---|---|
| Tip SHA | `d39528e9991f4a743fb3fca1dca9b2b21f81f5dd` |
| Recover PR | PR #8 (merge `60206ba` on archive main); tile-polish payload via PR #12 (merge `712ed62`) |
| Code in monorepo | `apps/board/frontend/src/lib/relativeTime.ts`, `apps/board/frontend/src/components/CardTile.tsx` |

Per-card cost chips and per-column dollar rollups. The feature commit `60206ba` was merged
directly into archive main via PR #8. The tile-polish dependency shipped via PR #12.
Do not resurrect; the cost chip and rollup logic is live at `apps/board/frontend/src/components/CardTile.tsx`.

---

### 2. feature/dashboard-polish

| Field | Value |
|---|---|
| Tip SHA | `e19a53406aa579cfca68a0fd1a8f12f0ce798a71` |
| Recover PR | PR #4 (merges `70b4815` + `bb044ba` onto archive main) |
| Code in monorepo | `apps/board/frontend/src/components/CardTile.tsx`, `apps/board/backend/scripts/seed-cards.ts`, `apps/board/backend/src/stories/demoInvoker.ts` |

Round-one polish pass: tile styling, card modal cleanup, kanban column layout, plus the
backend seeding script and offline demo invoker. Content-identical to the PR #4 ship
commits (`70b4815`, `bb044ba`). Main is AHEAD (absorbed all recover/* and grid PRs on top).
Do not resurrect; the seeding script lives at `apps/board/backend/scripts/seed-cards.ts`
and the demo invoker at `apps/board/backend/src/stories/demoInvoker.ts`.

---

### 3. feature/submit-story-surface

| Field | Value |
|---|---|
| Tip SHA | `acccf73e213d3c1dbbe8aa551d4ef0bf74366e23` |
| Recover PR | PR #3 (merge `84f8ea7`, feature commit `dfc1bd8` is a direct ancestor of archive main); PR #4 for the dashboard-polish payload merged into this branch |
| Code in monorepo | `apps/board/frontend/src/routes/SubmitStory.tsx`, `apps/board/backend/src/stories/demoInvoker.ts` |

Backend route, SSE stream, and dry-run review page for story submission. The feature commit
`dfc1bd8` is a strict ancestor of archive main (verified via `git merge-base --is-ancestor`).
Do not resurrect; the submit-story route lives at `apps/board/frontend/src/routes/SubmitStory.tsx`.

---

### 4. feature/project-lens

| Field | Value |
|---|---|
| Tip SHA | `d58200f439d4fb413d558f0178afc6e96ce82484` |
| Recover PR | PR #29 (merge `1be0329`); also depends on PR #27 (`169dce6`) and PR #28 (`684968d`) for cmdk and timeline payload merged into this branch |
| Code in monorepo | `apps/board/frontend/src/state/lens.ts`, `apps/board/frontend/src/components/FilterBar.tsx`, `apps/board/frontend/src/hooks/useKeyboard.ts` |

Group-by-project lens on the kanban with collapsed/expanded view per project, sitting on top
of the cmdk nav and timeline. All five unique non-merge commits have confirmed equivalents
on archive main (PRs #27-#30).
Do not resurrect; the lens state lives at `apps/board/frontend/src/state/lens.ts`.

---

### 5. feature/wip-limits

| Field | Value |
|---|---|
| Tip SHA | `416458350e4dd509a3a56cf68e79fe320571c8bb` |
| Recover PR | PR #30 (merge `21c1de2`); also covers PR #27 (`169dce6`), PR #28 (`684968d`), PR #31 (`0eaa0d5`) payload that was merged into this branch |
| Code in monorepo | `apps/board/frontend/src/state/wipLimits.ts`, `apps/board/frontend/src/state/wipLimits.test.ts` |

Soft per-column WIP limits with an inline editor. This branch was entangled with
`feature/dep-view` via a cross-merge (PR #18 merged dep-view INTO wip-limits). All seven
unique commits have equivalents on archive main (PRs #27-#31).
Do not resurrect; the WIP limit logic lives at `apps/board/frontend/src/state/wipLimits.ts`.

---

### 6. feature/dep-view

| Field | Value |
|---|---|
| Tip SHA | `3209c317a2aab00ed913f5bfc0b9793fd9712338` |
| Recover PR | PR #31 (merge `0eaa0d5`); full stack also covered by PRs #27 (`169dce6`), #28 (`684968d`), #29 (`1be0329`), #30 (`21c1de2`), #32 (`f724c79`), #33 (`bf9031a`), #34 (`5e1f0c1`) |
| Code in monorepo | `apps/board/frontend/src/components/DependencyView.tsx`, `apps/board/frontend/src/lib/depGraph.ts`, `apps/board/frontend/src/lib/sprintCapacity.ts`, `apps/board/frontend/src/routes/SprintDetail.tsx` |

The most-downstream branch in the entangled stack. Its first-parent spine contains
eleven commits covering the entire Roadmap 2.1-2.2 payload: dependency DAG modal, WIP
limits, project lens, Cmd-K palette, filter chips, card timeline, sprint backend list,
sprint detail, sprint capacity. All eleven have confirmed equivalents on archive main.
Do not resurrect; the dep graph lives at `apps/board/frontend/src/lib/depGraph.ts` and
the capacity panel at `apps/board/frontend/src/lib/sprintCapacity.ts`.

---

### 7. feature/tile-polish-v2

| Field | Value |
|---|---|
| Tip SHA | `4a70bb2f1d3e6ddb3c97cb15da3aee1688c0e6db` |
| Recover PR | PR #12 (merge `712ed62`); PR #19 (merge `e271a27`) covers the manual-rank payload that was merged into this branch |
| Code in monorepo | `apps/board/frontend/src/components/CardTile.tsx` |

Tile polish v2: click-to-copy short ID, age stamp, blocked-on dep badge. Shipped cleanly
via PR #12 on archive main.
Do not resurrect; the tile component is at `apps/board/frontend/src/components/CardTile.tsx`.

---

### 8. feature/manual-rank-v3

| Field | Value |
|---|---|
| Tip SHA | `032e7b9f75ebbd026b070dc5f166350d885e04e2` |
| Recover PR | PR #19 (merge `e271a27`); PR #27 (merge `169dce6`) covers the cmdk payload that landed on the tip commit |
| Code in monorepo | `apps/board/frontend/src/state/` (drag-rank logic) |

Manual card ranking v3. The tip commit added the cmdk merge (PR #27) on top of the
manual-rank payload; both are separately present on archive main.
Do not resurrect; manual rank state lives under `apps/board/frontend/src/state/`.

---

### 9. feature/cmdk-filter-views-v3

| Field | Value |
|---|---|
| Tip SHA | `53aa3300067fa45377e0b2dc917796f5eaa8faf4` |
| Recover PR | PR #27 (merge `169dce6`) |
| Code in monorepo | `apps/board/frontend/src/components/CommandPalette.tsx`, `apps/board/frontend/src/hooks/useKeyboard.ts` |

Cmd-K command palette with filter and navigation commands. Fully landed via PR #27.
Do not resurrect; the palette lives at `apps/board/frontend/src/components/CommandPalette.tsx`.

---

### 10. feature/card-event-timeline-v2

| Field | Value |
|---|---|
| Tip SHA | `d45a81d84b6e7bd3a1b8c2333e1a3cf0f6e3ec76` |
| Recover PR | PR #28 (merge `684968d`) |
| Code in monorepo | `apps/board/frontend/src/components/CardTimeline.tsx` (or equivalent timeline component) |

Per-card event timeline sidebar. Fully landed via PR #28.
Do not resurrect; the timeline component lives under `apps/board/frontend/src/components/`.

---

### 11. consolidate/main-polish-and-tunnel

| Field | Value |
|---|---|
| Tip SHA | `16569e8dcc106584142713c081627b27fc91c99b` |
| Recover PR | Strict ancestor of archive main -- no recover PR needed |
| Code in monorepo | N/A (infrastructure/consolidation commit, no feature files) |

A consolidation branch that was a strict ancestor of archive main at triage time.
Do not resurrect; it has been fully absorbed by archive main and therefore by `apps/board/`.

---

### 12. chore/sync-main-with-dashboard-polish

| Field | Value |
|---|---|
| Tip SHA | `4777002a90d8585f560384866e0290d3ab0e2755` |
| Recover PR | Content re-implemented on main via PRs #3 and #4; the PERSISTENT_TUNNEL.md on this branch is an older draft (see footnote below) |
| Code in monorepo | `apps/board/docs/PERSISTENT_TUNNEL.md` (superseded by the expanded version on main) |

A sync chore branch that carried early tunnel docs and a vite `allowedHosts` fix. All
substantive content is superseded by more complete versions on archive main.
Do not resurrect; the tunnel doc on archive main (and therefore in `apps/board/`) is
the canonical reference.

---

### 13. docs/dashboard-roadmap

| Field | Value |
|---|---|
| Tip SHA | `164450f534fbb7a657c1993d8c83f06ba8802015` |
| Recover PR | Strict ancestor of archive main -- no recover PR needed |
| Code in monorepo | `apps/board/docs/` (roadmap content absorbed into main docs) |

A docs-only branch that was a strict ancestor of archive main at triage time. No feature
code. Do not resurrect; the roadmap content is in `apps/board/docs/`.

---

## Known Footnotes

### PERSISTENT_TUNNEL.md hardcoded IDs (chore/sync-main-with-dashboard-polish)

The version of `PERSISTENT_TUNNEL.md` on `chore/sync-main-with-dashboard-polish` (tip
`4777002`) contains hardcoded infrastructure IDs: a specific Cloudflare tunnel UUID and
a Cloudflare account ID that appear as literal values rather than placeholders. The version
on archive main (and therefore in `apps/board/docs/PERSISTENT_TUNNEL.md`) is the expanded,
later revision that has redacted these to placeholder strings.

Risk level: LOW. The archive clone is read-only and not deployed. The branch will be pruned
from origin refs. No scrubbing is required. If those credentials have been rotated since
archival (they should have been), there is no additional action needed. If you have not
rotated them, do so as standard hygiene independent of this closeout.

---

## Archive PR Dependency Map (reference)

```
archive main (tip 04454e8)
 |-- PR #3  submit-story-surface      --> dfc1bd8 / merge 84f8ea7
 |-- PR #4  dashboard-polish          --> 70b4815 + bb044ba / merge dfc1bd8-era
 |-- PR #8  cost-surfaces             --> 60206ba
 |-- PR #12 tile-polish-v2            --> 712ed62
 |-- PR #19 manual-rank-v3            --> e271a27
 |-- PR #27 recover/cmdk-filter-views-v3      --> 169dce6
 |-- PR #28 recover/card-event-timeline-v2    --> 684968d
 |-- PR #29 recover/project-lens              --> 1be0329
 |-- PR #30 recover/wip-limits                --> 21c1de2
 |-- PR #31 recover/dep-view                  --> 0eaa0d5
 |-- PR #32 recover/sprint-backend-list       --> f724c79
 |-- PR #33 recover/sprint-detail             --> bf9031a
 |-- PR #34 recover/sprint-capacity           --> 5e1f0c1
```
