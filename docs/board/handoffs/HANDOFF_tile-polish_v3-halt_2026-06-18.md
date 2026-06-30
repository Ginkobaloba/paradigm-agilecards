# Handoff: tile-polish (v3-halt sweep, 2026-06-18)

**Feature slug:** `tile-polish`
**Branches in scope:** `feature/tile-polish`, `feature/tile-polish-v2`
**Canonical:** `feature/tile-polish-v2`
**Retired by this sweep:** `feature/tile-polish`

## Feature intent

Three small but high-frequency-of-use touches on the card tile component: a click-to-copy short ID affordance, an age stamp that updates relative to now, and a "blocked-on" dependency badge surfaced from the dep graph. Roadmap item 1.7. The work is small enough to be a one-PR delivery and lives almost entirely in `frontend/src/components/CardTile.tsx`, with small assists in `frontend/src/lib/relativeTime.ts` and `frontend/src/lib/depGraph.ts`. It is also the bottom of the recovery stack, which means every other branch in this sweep ultimately rebases on top of it.

## What v1 (`feature/tile-polish`) tried, and where it stopped

v1 was PR #9, the original land. It sat on top of `feature/cost-surfaces` in the original stack and earned MERGED status from GitHub on 2026-05-21 the same way the rest of that stack did: stacked-PR base juggling that did not actually bring the upstream commits onto main. The head commit `a5465ce` still exists on the remote, and the tile-polish feature itself was complete and tested when it stranded. It stopped because the stacked-PR pattern lied to GitHub about merge completeness, not because the implementation had any pending work.

## What v2 (`feature/tile-polish-v2`) tried, and where it stopped

v2 was the recovery cherry-pick. Same three touches, fresh-stamped onto a clean stack off current main, opened as PR #12 on 2026-05-22. It is the bottom of the recovery stack: manual-rank-v3 sits on it, cmdk-filter-views-v3 sits on that, card-event-timeline-v2 caps the stack. Last commit `4a70bb2` on 2026-05-23. It stopped because the recovery stack as a whole sat unmerged for four weeks. The tile-polish slot itself was ready to ship the day it was opened.

## Which version was kept canonical, and why

**v2 wins.** The tile-polish implementation is identical between v1 and v2; the only difference is which base they sit on. v2 is on a fresh-off-main recovery base, v1 is on the stranded stack. Picking v2 means picking the version that everything else in the stack already chains against, and it means picking the version with the lowest delta to current main (only 2 commits behind, the smallest of any branch in this sweep).

## What would need to happen to ship the canonical version

Tile-polish is the first merge in the bottom-up sequence. Merge PR #12 to main, let GitHub auto-retarget PR #13 (manual-rank-v3) to main, repeat. Worth a fresh CI pass first because the underlying main is dirty (see retro move 1 about recovering the polis-shape working tree). Of the four canonical branches in this sweep, this one has the smallest blast radius and the highest "easy click" potential. Ship it first, both because the rest of the stack needs it shipped to land cleanly, and because banking one of these merges releases the rest of the chain by a real, visible step.

## Do not open a v3

Do NOT open a `feature/tile-polish-v3` (or higher) branch without first writing a paragraph in `docs/handoffs/` explaining why the canonical version (`feature/tile-polish-v2`) cannot be shipped as-is. This feature has a smaller delta than any other in this sweep; if it cannot be shipped as-is, the blocker is almost certainly the working tree on main, not the branch itself, and the fix is to recover main, not to spin a v3.
