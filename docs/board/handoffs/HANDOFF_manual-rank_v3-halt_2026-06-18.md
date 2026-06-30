# Handoff: manual-rank (v3-halt sweep, 2026-06-18)

**Feature slug:** `manual-rank`
**Branches in scope:** `feature/manual-rank`, `feature/manual-rank-v2`, `feature/manual-rank-v3`
**Canonical:** `feature/manual-rank-v3`
**Retired by this sweep:** `feature/manual-rank`, `feature/manual-rank-v2`

## Feature intent

Drag-to-reorder cards inside a single column, plus a per-column sort dropdown so the operator can swap between manual rank, age, cost, and priority without leaving the board. Roadmap item 1.4. The work touches `backend/src/db/ranks.ts` (the persistence layer), `backend/src/routes/ranks.ts` (the wire format), plus the kanban-side drag handlers and the column header dropdown on the frontend. The rank table is additive (no schema rewrite). Persistence is intentionally optimistic: the UI commits the new order locally and lets the backend confirm asynchronously, so a slow disk does not stall the drag.

## What v1 (`feature/manual-rank`) tried, and where it stopped

v1 was PR #10, the original land of drag-to-reorder. It sat on top of `feature/tile-polish` in the original stack and inherited the same fate as the rest of that stack: GitHub flipped its status to MERGED on 2026-05-21 when the base branch chain reshuffled, but the work itself never made it to main. The branch still holds the commits (head `cf6daaa`, last commit 2026-05-21). It stopped because the stacked-PR auto-retarget trap took out the whole chain, not because anything in the rank implementation went wrong. Tests were green at the time of stranding.

## What v2 (`feature/manual-rank-v2`) tried, and where it stopped

v2 was the recovery cherry-pick. Same rank implementation, fresh-stamped onto a clean stack off current main, opened as PR #13 on 2026-05-22 with `feature/tile-polish-v2` as its base. The cmdk-filter-views-v2 stack chained on top of it via PR #14. Last commit `4ca01ac` on 2026-05-23. It stopped because the recovery stack as a whole sat unmerged for the same reason v1 stopped: Drew did not get back to it. Once project-lens (PR #16) landed and the stack got rebased to absorb it, v2 became stale relative to v3.

## What v3 (`feature/manual-rank-v3`) tried, and where it stopped

v3 is v2 plus the project-lens chain merged in via PR #20 from `feature/cmdk-filter-views-v3`. The rank implementation itself is identical to v2 and v1 at the source level; only the surrounding stack changed. v3 is the current head of the rank slot in the recovery stack, and it is what `feature/card-event-timeline-v2` ultimately chains against. Last commit `032e7b9` on 2026-05-23. It stopped because the recovery stack has been open for four weeks and nobody has merged it.

## Which version was kept canonical, and why

**v3 wins.** Same logic as cmdk: v3 contains the rank implementation (unchanged across all three versions) plus the lens stack that the rest of the recovery chain depends on. v1 and v2 are subsets in terms of what is integrated; the unique rank work is identical across the three. Keeping the lower versions around adds zero implementation value and adds friction the next time someone touches this corner of the board.

## What would need to happen to ship the canonical version

Bottom-up merge of the recovery stack: tile-polish-v2 first, then manual-rank-v3, then cmdk-filter-views-v3, then card-event-timeline-v2. The rank slot itself has no in-feature work left to do. Two things worth checking before the merge: first, `backend/src/db/ranks.test.ts` should still pass against current SQLite-bindings versions because the schema migration sequence might have moved under it during the four-week stall. Second, the optimistic-commit behavior on the frontend should be smoke-tested against a live SSE channel because the test suite uses a mock channel and the real one has a known reconnect quirk during slow saves.

## Do not open a v4

Do NOT open a `feature/manual-rank-v4` (or higher) branch without first writing a paragraph in `docs/handoffs/` explaining why the canonical version (`feature/manual-rank-v3`) cannot be shipped as-is. Three versions of identical implementation already live on the remote; a fourth without a written justification would push this from a process accident into a habit. Ship v3 or write the paragraph.
