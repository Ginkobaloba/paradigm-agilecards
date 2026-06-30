# Handoff: cmdk-filter-views (v3-halt sweep, 2026-06-18)

**Feature slug:** `cmdk-filter-views`
**Branches in scope:** `feature/cmdk-filter-views`, `feature/cmdk-filter-views-v2`, `feature/cmdk-filter-views-v3`
**Canonical:** `feature/cmdk-filter-views-v3`
**Retired by this sweep:** `feature/cmdk-filter-views`, `feature/cmdk-filter-views-v2`

## Feature intent

A Cmd-K command palette over the board, plus a chip-style filter bar above it, plus saved-view persistence, plus a keyboard nav layer that lets you drive the dashboard without the mouse. Roadmap items 1.1, 1.2, 1.5, and 1.6 in the near-term plan. The feature touches `frontend/src/state/filters.ts`, `frontend/src/lib/fuzzy.ts`, `frontend/src/components/CommandPalette.tsx`, `FilterBar.tsx`, `FilterChip.tsx`, `ViewMenu.tsx`, `Cheatsheet.tsx`, and `hooks/useKeyboard.ts`. It is the operator's primary navigation surface once the board has more than two columns of work in flight.

## What v1 (`feature/cmdk-filter-views`) tried, and where it stopped

v1 was the original PR #11 land of the cmdk feature on top of the original `feature/manual-rank`, which sat on top of `feature/tile-polish`, which sat on `feature/cost-surfaces`. PR #11 got marked MERGED by GitHub on 2026-05-20 but the work never made it to `main` because of the stacked-PR auto-retarget trap: each upstream PR's base was the next branch down, and when PR #8 (cost-surfaces) merged to main, GitHub re-targeted #9 through #11's bases without bringing the upstream commits along. The branches themselves still hold the work (last commit `0209b4b` on 2026-05-20) but they are functionally stranded. They stopped because GitHub lied about merging them and nobody noticed for a day.

## What v2 (`feature/cmdk-filter-views-v2`) tried, and where it stopped

v2 was the recovery cherry-pick. Same cmdk implementation, fresh-stamped onto a clean stack off current main, opened as PR #14 on 2026-05-22. It also picked up the `card-event-timeline` merge on top (PR #15 from v1's timeline branch into v2's cmdk branch). Last commit `0a42c13` on 2026-05-23. It stopped because Drew did not get back to merging the recovery stack, and once project-lens (PR #16) landed independently, the stack got re-rebased again, producing v3.

## What v3 (`feature/cmdk-filter-views-v3`) tried, and where it stopped

v3 is v2 plus the project-lens integration plus the timeline-v2 chain merged in. It is the current head of the recovery stack as of this sweep. The cmdk implementation itself is unchanged from v1; v3 is a re-stamped version of the same commits on a stack that now includes lens. Last commit `53aa330` on 2026-05-23. It stopped for the same reason every other branch in this family stopped: the recovery stack has been sitting open for four weeks, nobody has merged it, the board's working tree has drifted into the polis-style dirty state, and the stack accumulated more rebases instead of getting shipped.

## Which version was kept canonical, and why

**v3 wins.** v3 contains everything v1 and v2 contain (the cmdk implementation is identical at the source level; only the commit hashes differ because of the re-stamps) plus the lens integration that the rest of the recovery stack already depends on. Choosing v2 would force a re-resolve against v3 the moment the lens stack lands, and choosing v1 means re-running the entire recovery cherry-pick. v3 is the version that the rest of the recovery stack (timeline-v2, manual-rank-v3) already chains against.

## What would need to happen to ship the canonical version

Same stack-up-from-the-bottom dance as the timeline handoff: tile-polish-v2 lands first, manual-rank-v3 next, cmdk-filter-views-v3 third, card-event-timeline-v2 last. The cmdk feature itself has no dependencies that block the merge; the blocker is the underlying stack. Worth fresh CI before merging because four weeks is enough drift for `npm` to have moved under the test runner. Also worth a manual scan of `frontend/src/lib/fuzzy.test.ts` because the fuzzy matcher is the highest-blast-radius piece in this PR and the test coverage there should still be the green it was on 2026-05-23.

## Do not open a v4

Do NOT open a `feature/cmdk-filter-views-v4` (or higher) branch without first writing a paragraph in `docs/handoffs/` explaining why the canonical version (`feature/cmdk-filter-views-v3`) cannot be shipped as-is. This feature now has three full versions of the same implementation living on the remote because nobody clicked merge. Opening a v4 to "freshen the rebase" is not a strategy, it is the avoidance behavior the v3-halt rule was written to stop. Ship v3 or write the paragraph.
