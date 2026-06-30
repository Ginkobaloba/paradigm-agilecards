# Saved Views v0: completion scope (living draft)

Date: 2026-06-02
Author: Claude (Sprint 2 scoping, dispatched via orchestrator)
Repo: `agile-cards-board`
Branch: `feat/saved-views-v0`
Status: **DRAFT scope, holding for Drew's go.** This PR carries the scope,
not the implementation. Sprint 2 build proceeds on approval. Adjust freely;
this is a living draft.

---

## 0. The correction this scope is built on

The 2026-06-01 sprint plan (`outputs/SPRINT_PLAN_2026-06-01_grid-and-next.md`)
picked saved views as Sprint 2's lead item on the assumption it was
greenfield. That assumption was wrong. Verified against the merged `main`
(post PR #36):

- **Backend is done and tested.** `backend/src/routes/views.ts` exposes full
  token-scoped CRUD (`GET/POST/PATCH/DELETE /api/views`, plus `GET /views/:id`),
  with name validation, a 16 KB opaque-JSON payload cap, and a UNIQUE
  `(token_id, name)` constraint. `backend/src/db/views.ts` is the data layer,
  covered by `backend/src/db/views.test.ts`. The payload is stored opaquely,
  so the backend needs no change to hold a richer view bundle.
- **The frontend menu exists and is mounted.** `frontend/src/components/ViewMenu.tsx`
  is a complete dropdown (list, load, save-new, update-current, delete,
  copy-share-link). It is rendered in `Header.tsx`, so it shows on every route.

So saved views is roughly 90% built. v0 is a **completion**, not a from-scratch
sprint. That materially changes what Sprint 2's body should be (see section 5).

---

## 1. The actual gap

What the existing ViewMenu persists today: **only the filter state**
(`FilterState` from `state/filters.ts`). `handleSaveNew` writes `filters` as
the payload; `handleLoad` does `setAll({ ...EMPTY_FILTERS, ...payload })`.

What it does NOT capture, and therefore what a "saved view" silently drops:

1. **The lens `groupBy`** (`state/lens.ts`, `none` | `project`). A view saved
   while grouped by project loads back ungrouped.
2. **The grid axes.** `Grid.tsx` holds `xAxis` / `yAxis` as local `useState`
   (lines 58-59). They live nowhere a view can reach, so a grid configuration
   (for example, cost-vs-points) cannot be saved or restored at all. This is
   the grid-specific tie-in the sprint plan wanted from saved views, and it is
   the most valuable missing piece now that the grid has shipped.

Two smaller gaps:

3. **No frontend test** for ViewMenu (the backend has `views.test.ts`; the
   component has nothing).
4. **URL filter-sync is kanban-only** (`App.tsx` `useFilterUrlSync` early-returns
   unless `pathname === "/"`). The copy-share-link in ViewMenu encodes filters
   into a URL that only re-hydrates on the kanban route. Out of v0 scope, noted
   for v1.

---

## 2. v0 deliverables

**Goal:** a saved view captures and restores the full view state a user can see
-- filters, grouping, and grid axes -- without breaking the views already saved
under the current payload shape.

1. **Versioned view bundle.** Promote the saved payload from a bare
   `FilterState` to:
   ```
   interface ViewBundleV1 {
     v: 1;
     filters: FilterState;
     groupBy: GroupBy;            // 'none' | 'project'
     grid?: { xAxis: AxisKey; yAxis: AxisKey };
   }
   ```
   A normalizer reads either shape: a bare `FilterState` (old rows, no `v`
   field) loads as `{ filters, groupBy: 'none', grid: undefined }`. No backend
   change, no migration job -- old rows just resolve through the normalizer.

2. **Lift grid axes into a store.** Move `xAxis` / `yAxis` out of `Grid.tsx`
   local state into a small zustand store (`state/gridAxes.ts`), mirroring the
   `useFilters` / `useLens` convention already in the codebase. `Grid.tsx`
   consumes from the store; ViewMenu reads and writes it. The default stays
   `x = cost`, `y = stakes`.

3. **ViewMenu round-trips the bundle.** `handleSaveNew` /
   `handleUpdateCurrent` write the bundle; `handleLoad` applies filters,
   `groupBy`, and grid axes through the normalizer.

4. **Frontend tests** (`ViewMenu.test.tsx`): save captures the full bundle;
   load applies filters + groupBy + axes; a legacy bare-`FilterState` payload
   loads as filters-only without throwing. Target: frontend 149 -> ~155,
   backend 77 unchanged.

5. **Re-confirm backend stays green.** The bundle is well under the 16 KB cap;
   `views.test.ts` is unaffected. Run both suites to prove it.

---

## 3. Out of scope for v0 (named, not silently dropped)

- **"Sort" and "columns"** from the classic saved-view definition. The board
  has no per-column saved sort or column-config state to capture yet (kanban
  rank-sort is implicit, not a user-set knob). v1, once those knobs exist.
- **Cross-route URL sync** for share-links (gap 4 above). v1.
- **Per-view default / auto-apply on load.** A view is loaded explicitly; no
  "open the board into my default view" yet. v1.

---

## 4. Tier and risk

**Tier 2.** It touches a write surface (the view payload), but it is
token-scoped, carries no money, no runner interaction, no repo-visibility
change. Two-reviewer consensus, auto-merge on approval -- same gate Sprint 1
(PR #36) cleared. Nothing here is Tier 3.

Main risk is the backward-compat normalizer: if it mishandles an old bare
payload, a previously-saved view loads wrong. The legacy-payload test in
deliverable 4 is the guard.

---

## 5. The strategic update for Drew

Because saved views was already 90% built, this sprint is a small completion
(estimate: a day of build plus review), not the multi-day item the plan
assumed. That frees Sprint 2's real body to move to **Option B (triage inbox +
backlog grooming)** sooner -- the surfaces the cards-strategy north star
actually points at (keeping the claimable-card pool saturated).

Recommended sequencing, unchanged in spirit but compressed:
1. Land this saved-views v0 completion (small, Tier 2).
2. Roll straight into B: `/triage` inbox and `/backlog` grooming.

No fork needs deciding to proceed -- this is a completion of work already
chosen. The one thing worth your eyes is the compression itself: saved views
is nearly free, so B starts almost immediately.

---

## 6. What this document is

A living draft scope, opened as a draft PR so the corrected picture (saved
views already exists) is visible before any code lands. On your go, the v0
deliverables in section 2 implement against this scope.
