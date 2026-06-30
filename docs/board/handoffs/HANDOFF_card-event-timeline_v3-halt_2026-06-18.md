# Handoff: card-event-timeline (v3-halt sweep, 2026-06-18)

**Feature slug:** `card-event-timeline`
**Branches in scope:** `feature/card-event-timeline`, `feature/card-event-timeline-v2`
**Canonical:** `feature/card-event-timeline-v2`
**Retired by this sweep:** `feature/card-event-timeline`

## Feature intent

A collapsible Timeline panel inside the card detail modal that surfaces the runner's lifecycle for that card. Specifically the event types `discovered`, `started`, `heartbeat`, `finished`, `verifier_called`, `cascade`, `released`, `merge_status_changed`, and `status_changed`. Storage is a schema-v4 SQLite table that ships additively (no data motion on existing tables), and the panel is patched live over the dashboard's SSE channel. The architectural call worth keeping is that events get derived from frontmatter deltas rather than emitted by the runner directly, which keeps the runner contract simple and turns this module into a thin adapter on the day the runner ships a structured event stream. The feature lines up with roadmap item 2.5.

## What v1 (`feature/card-event-timeline`) tried, and where it stopped

v1 was the original land of the timeline feature on top of the recovered cmdk/manual-rank/tile-polish stack. It was the head of a four-PR stack (PRs #12 through #15) where each branch sat on top of the next one down. The work shipped to PR #15 on 2026-05-22, then froze. It did not get merged to main because the recovery stack underneath it (#12, #13, #14) never finished landing through the auto-retarget chain. PR #15 has been open with a clean test run (backend 57 pass, frontend 62 pass) since the day it was authored, last commit `307ce6a` on 2026-05-22, four weeks before this sweep. The reason it stopped is the same reason the v2 branch exists at all: Drew did not get back to clicking merge on the stack, and the stalled stack got rebased on top of project-lens once that landed, producing v2.

## What v2 (`feature/card-event-timeline-v2`) tried, and where it stopped

v2 is the same timeline implementation cherry-picked fresh onto a stack that also includes the project-lens feature (PR #16). The unique additions over v1 are `frontend/src/state/lens.ts`, `frontend/src/state/lens.test.ts`, and the integration points in `Column.tsx`, `FilterBar.tsx`, and `Kanban.tsx`. v2 is the head of the recovery stack as it stands today, sitting on top of `feature/cmdk-filter-views-v3`, which sits on `feature/manual-rank-v3`, which sits on `feature/tile-polish-v2`. v2 stopped for the same reason v1 stopped: Drew did not merge. Last commit `d45a81d` on 2026-05-23, also four weeks before this sweep.

## Which version was kept canonical, and why

**v2 wins.** v2 is a strict superset of v1 in terms of intent and content: same timeline implementation (the cherry-pick is a re-stamp, not a redesign), plus the project-lens integration that landed alongside it. v1 has no work that is not in v2. Keeping the older branch around adds nothing except another option that nobody is choosing. v2 is the version the existing handoff doc (`HANDOFF_2026-05-22_recovery-and-timeline.md`) points at as the live PR head.

## What would need to happen to ship the canonical version

The stack needs to land in dependency order, bottom up: `feature/tile-polish-v2` first, then `feature/manual-rank-v3`, then `feature/cmdk-filter-views-v3`, then `feature/card-event-timeline-v2` on top. Concretely, that is PRs #12, #13, #14, then #15-equivalent (#21 if a new PR was opened for v2) merged in order. GitHub should auto-retarget each upstream PR's base to main as the next one below it merges, which is the same mechanism that misled the original first-attempt land, but applied correctly. Before merging, a fresh CI pass is worth running because the branch is four weeks behind main and the working tree on `main` is in the polis-style 116-dirty state that the 2026-06-18 retro flagged. Bringing main to a clean state (move 1 in the retro) is a precondition for any sane merge.

## Do not open a v4

Do NOT open a `feature/card-event-timeline-v3` or `-v4` branch without first writing a paragraph in `docs/handoffs/` explaining why the canonical version (`feature/card-event-timeline-v2`) cannot be shipped as-is. The cost of re-cherry-picking the same commits onto yet another base because the prior land got stalled is the exact pattern that produced this sweep. Make the merge happen, or document why it cannot, before spawning another version.
