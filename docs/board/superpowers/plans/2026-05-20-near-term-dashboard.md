# Near-Term Dashboard Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the eight near-term features from `docs/DASHBOARD_ROADMAP.md` (sections 1.1-1.8) as four stacked, reviewable PRs grouped by theme, leaving each PR open for Drew's review in the morning.

**Architecture:** Backend is Express + TypeScript + better-sqlite3 + chokidar; frontend is React + Vite + TS + Tailwind + Radix + dnd-kit + Zustand. Cards-of-record stay on disk (`.md` + frontmatter); SQLite holds dashboard-only state (tokens, sprints, retros, and the new tables this plan adds). The four PRs stack sequentially to share overlap on `CardTile`, `Column`, `Kanban`, and the store.

**Tech Stack:** Express 4 / better-sqlite3 11 / React 18 / Vite 5 / Tailwind 3 / Radix Dialog + Tooltip / dnd-kit 6 / Zustand 5 / date-fns 4 / vitest 2 / node:test (tsx).

---

## PR Stack and Branches

| # | Branch (base) | Roadmap Items | Theme |
|---|---|---|---|
| 1 | `feature/cost-surfaces` (off `origin/main`) | 1.3 dollar-cost chip, 1.8 column cost rollup | Money on every surface |
| 2 | `feature/tile-polish` (off PR 1) | 1.7 copy-id, age, dependency badge | Per-tile polish |
| 3 | `feature/manual-rank` (off PR 2) | 1.4 rank-in-column + sort dropdown | Manual ordering |
| 4 | `feature/cmdk-filter-views` (off PR 3) | 1.1 Cmd-K, 1.2 filter chips, 1.5 saved views, 1.6 keyboard | Navigation and filtering |

Drew reviews and merges in main-branch order in the morning. Every PR has its own typecheck + tests green and a self-contained branch base. Roadmap doc lands on PR 1 so reviewers can see what we're building toward.

---

## File Map (full stack)

### Backend additions

- `backend/src/cost/rates.ts` (new) — model-rate table, `ratesByModel()`, `costForTokens(tokens, model)`. PR 1.
- `backend/src/cost/rates.test.ts` (new) — node:test, table coverage. PR 1.
- `backend/src/routes/rates.ts` (new) — `GET /api/rates`. PR 1.
- `backend/src/server.ts` (modify) — mount rates router. PR 1. Mount ranks router PR 3. Mount views router PR 4.
- `backend/src/db/sqlite.ts` (modify) — schema v2 (`card_rank`), schema v3 (`saved_views`). PR 3 + PR 4.
- `backend/src/db/ranks.ts` (new) — getRanks / setRank / removeRank / nextRank. PR 3.
- `backend/src/db/ranks.test.ts` (new) — mid-point and append behavior. PR 3.
- `backend/src/routes/ranks.ts` (new) — `GET /api/ranks`, `POST /api/cards/:id/rank`. PR 3.
- `backend/src/db/views.ts` (new) — list/get/create/update/delete saved views per token. PR 4.
- `backend/src/routes/views.ts` (new) — CRUD endpoints. PR 4.

### Frontend additions

- `frontend/src/lib/cost.ts` (new) — pure cost compute + format. PR 1.
- `frontend/src/lib/cost.test.ts` (new). PR 1.
- `frontend/src/hooks/useRates.ts` (new) — fetch + cache rates. PR 1.
- `frontend/src/lib/parseCard.ts` (modify) — `cardEstTokens`, `cardActualTokens`, `cardCostCap`, `cardClaimedBy`, `cardMergeStatus`, `cardAge`. PR 1, expanded PR 2 + 4.
- `frontend/src/lib/relativeTime.ts` (new) — `"2h ago"`, `"stale 4d"`. PR 2.
- `frontend/src/components/CardTile.tsx` (modify) — cost chip PR 1; click-to-copy id, age, dep badge PR 2.
- `frontend/src/components/Column.tsx` (modify) — cost rollup PR 1; sort dropdown + drag-within-column listener PR 3.
- `frontend/src/routes/Kanban.tsx` (modify) — rank-aware sort PR 3; filter-aware selector PR 4.
- `frontend/src/state/store.ts` (modify) — rank slice PR 3; filter + view slices PR 4.
- `frontend/src/state/ranks.ts` (new) — rank store + selector. PR 3.
- `frontend/src/state/filters.ts` (new) — filter store + selector. PR 4.
- `frontend/src/components/FilterBar.tsx` (new). PR 4.
- `frontend/src/components/FilterChip.tsx` (new). PR 4.
- `frontend/src/components/CommandPalette.tsx` (new) — Cmd-K. PR 4.
- `frontend/src/hooks/useKeyboard.ts` (new) — global shortcuts. PR 4.
- `frontend/src/lib/views.ts` (new) — view URL encode/decode + REST. PR 4.
- `frontend/src/components/ViewMenu.tsx` (new) — pick/save/share. PR 4.
- `frontend/src/components/Header.tsx` (modify) — wire palette trigger, view menu. PR 4.

### Doc additions

- `docs/DASHBOARD_ROADMAP.md` — cherry-pick from `origin/docs/dashboard-roadmap`. PR 1.
- Each PR includes a short handoff note in its PR body (no separate file unless the feature warrants it).

---

## Fork decisions, made for this stack

The roadmap surfaced explicit forks. The decisions baked into this plan:

- **Fork A (rank storage):** A2 — SQLite, not frontmatter. Disk file stays the work definition; rank is presentation. Confirmed.
- **Fork C (filter UX):** C1 — chip builder. No JQL. Confirmed.
- **Fork F (cost computation):** F2 — backend holds the rate table; dashboard computes on demand from tokens. Confirmed. Rate table is hard-coded in code for v1 (no yaml yet); changing rates is a one-line code edit.
- **Cost-cap enforcement (D):** out of scope here; this PR ships *display only* (color-stepped chip), no enforcement. Reflects "Effort: M, display only for v1" from the roadmap.

Non-roadmap decisions:

- **Saved-view storage:** SQLite, keyed by `token_id`. Token is the user proxy. Sharable via URL-encoded query string; logged-in user sees their own saved list plus URL-imported views.
- **Cmd-K library:** No new dep. ~80 lines of fuzzy matcher inline. The board has hundreds of cards, not tens of thousands; substring + lowercased title/id match is enough.
- **Drag-within-column for rank:** dnd-kit's `SortableContext` already exposes index changes; we just need to detect "dropped onto a card vs onto a column" in `onDragEnd` and compute a new rank between neighbors.

---

## Task list

The task list intentionally tracks PR-level milestones rather than every-step micro-tasks; the implementation steps live inline as the dispatched subagents work each PR.

### Task 1: Cherry-pick the roadmap doc and start the cost branch (PR 1)

**Files:**
- Create: `docs/DASHBOARD_ROADMAP.md` (from `origin/docs/dashboard-roadmap`)
- Create: `backend/src/cost/rates.ts`, `backend/src/cost/rates.test.ts`, `backend/src/routes/rates.ts`, `frontend/src/lib/cost.ts`, `frontend/src/lib/cost.test.ts`, `frontend/src/hooks/useRates.ts`
- Modify: `backend/src/server.ts`, `backend/src/routes/auth.ts` (if needed for rates exemption — probably not, rates are auth-gated), `frontend/src/components/CardTile.tsx`, `frontend/src/components/Column.tsx`, `frontend/src/lib/parseCard.ts`, `backend/package.json` (test script may need updating)

- [ ] Branch off `origin/main`: `git switch -c feature/cost-surfaces origin/main`
- [ ] `git restore --source=origin/docs/dashboard-roadmap -- docs/DASHBOARD_ROADMAP.md`
- [ ] Implement rates module + endpoint with tests
- [ ] Implement cost compute + format on the frontend with tests
- [ ] Wire tile and column-header surfaces
- [ ] `npm --prefix backend run typecheck && npm --prefix backend test`
- [ ] `npm --prefix frontend run typecheck && npm --prefix frontend test`
- [ ] Push and open PR

### Task 2: Tile polish (PR 2)

**Files:**
- Create: `frontend/src/lib/relativeTime.ts`, `frontend/src/lib/relativeTime.test.ts`
- Modify: `frontend/src/components/CardTile.tsx`, `frontend/src/lib/parseCard.ts`, `frontend/src/state/store.ts` (selector for "are deps done")

- [ ] Branch: `git switch -c feature/tile-polish feature/cost-surfaces`
- [ ] Add relative-time helper using date-fns formatDistanceToNowStrict
- [ ] Make short-id click-to-copy (clipboard API, fallback `document.execCommand`)
- [ ] Stop propagation on copy click so the tile click doesn't also open the modal
- [ ] Add "blocked on N" badge; clickable to jump to first unmet dep
- [ ] Tests for relativeTime
- [ ] typecheck + test, push, open PR

### Task 3: Manual rank (PR 3)

**Files:**
- Create: `backend/src/db/ranks.ts`, `backend/src/db/ranks.test.ts`, `backend/src/routes/ranks.ts`, `frontend/src/state/ranks.ts`
- Modify: `backend/src/db/sqlite.ts` (schema v2), `backend/src/server.ts`, `frontend/src/lib/api.ts`, `frontend/src/state/store.ts`, `frontend/src/routes/Kanban.tsx`, `frontend/src/components/Column.tsx`

- [ ] Branch: `git switch -c feature/manual-rank feature/tile-polish`
- [ ] DB migration v2: `card_rank(card_id PK, status, rank REAL, updated_at)`
- [ ] `setRank` uses midpoint between neighbors; `nextRank(status)` for new arrivals
- [ ] Routes with tests
- [ ] Frontend selector: per-status, sort by rank if present else by id
- [ ] dnd-kit `onDragEnd`: same-status drop -> POST rank; cross-status drop -> existing move
- [ ] Sort dropdown stub on Column header (Rank default; Created; Tier; Cost; Heartbeat)
- [ ] typecheck + test, push, open PR

### Task 4: Cmd-K + filter + saved views + keyboard (PR 4)

**Files:**
- Create: `backend/src/db/views.ts`, `backend/src/routes/views.ts`, `frontend/src/state/filters.ts`, `frontend/src/components/FilterBar.tsx`, `frontend/src/components/FilterChip.tsx`, `frontend/src/components/CommandPalette.tsx`, `frontend/src/hooks/useKeyboard.ts`, `frontend/src/components/ViewMenu.tsx`, `frontend/src/lib/views.ts`
- Modify: `backend/src/db/sqlite.ts` (schema v3), `backend/src/server.ts`, `frontend/src/App.tsx`, `frontend/src/components/Header.tsx`, `frontend/src/routes/Kanban.tsx`, `frontend/src/state/store.ts`, `frontend/src/lib/api.ts`

- [ ] Branch: `git switch -c feature/cmdk-filter-views feature/manual-rank`
- [ ] DB v3: `saved_views(id, token_id, name, filters_json, sort, columns_json, created_at)`
- [ ] Views CRUD routes with tests
- [ ] Filter store + selectors + URL sync
- [ ] FilterBar with chips: project, batch, claimed_by, tier, stakes, pin, extended_thinking, merge_status
- [ ] CommandPalette: fuzzy search across cards + commands; recent-list backed by localStorage
- [ ] Global shortcuts: `Cmd-K` / `Ctrl-K`, `/` focuses palette, `F` toggles filter bar, `Esc` closes both, `?` shows cheatsheet
- [ ] ViewMenu in header: list saved views, save current, share URL
- [ ] typecheck + test, push, open PR

### Task 5: Verify the whole stack

- [ ] Re-run typecheck + test on the top branch (`feature/cmdk-filter-views`)
- [ ] Smoke-run the backend on an alt port (NOT 4070) against a fresh SQLite path and seeded `todo/` tree to confirm no regressions on `/api/cards`, drag-drop, SSE.
- [ ] Confirm `submit-story` page still mounts (the route is untouched).
- [ ] Write a session handoff in `docs/handoffs/HANDOFF_2026-05-20_near-term-roadmap.md`.

---

## Risks and explicit non-goals

- **No cost-cap *enforcement*.** Display only. The governor (3.1) is horizon-3 work.
- **No backlog or triage surfaces.** Mid-term scope.
- **Saved-view sharing is URL-encoded, not signed.** A pasted link can carry an arbitrary view; that's fine because the underlying API is still token-gated.
- **Saved views are per-token, not multi-tenant.** Multi-tenant tokens (`b044-03-multi-tenant-tokens`) is its own card and unblocks proper user accounts.
- **Keyboard "focused card" gestures** (`S` opens status picker on focused card) are deferred. We ship global shortcuts; per-card focus management would need a focused-card concept in the store and visible focus styling on tiles. Both are non-trivial and out of scope here. The other shortcuts ship.

---

## Verification commands (every PR)

```
npm --prefix backend run typecheck
npm --prefix backend test
npm --prefix frontend run typecheck
npm --prefix frontend test
```

Each PR holds these green at HEAD.
