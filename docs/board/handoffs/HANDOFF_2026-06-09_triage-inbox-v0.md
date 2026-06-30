# Handoff: triage inbox v0 (2026-06-09)

Session: Fable run dispatched from Dispatch, CTO-mode velocity rules.
Companion handoff in the sibling repo:
`agile-cards/docs/handoffs/HANDOFF_2026-06-09_gate-chunk-3-calibration.md`
(gate chunk 3 merged there the same session).

## What this session did

1. **Confirmed saved views v0 shipped.** PR #37's squash included both
   the scope doc AND the implementation commit despite its
   "[scope-only, holding for go]" title -- `savedView.ts`,
   `gridAxes.ts`, the ViewMenu bundle round-trip, and the tests are
   all on main. Local main was just behind; synced and pruned 14
   stale local branches.
2. **Built and merged the triage inbox (PR #38)** -- roadmap 2.4, the
   first half of Sprint 2's body (Option B per the saved-views scope
   doc section 5 and the 2026-06-01 sprint plan in `outputs/`).
   - `/triage` route: staged submit-story batches resolved per card.
     Title, body excerpt, tier, $ estimate, "Similar to" dedup
     (token-Jaccard, `lib/similarity.ts`; embeddings are roadmap 3.6).
   - Actions: promote (into backlog with rank), merge (absorbed into
     the target card as an "Absorbed from triage" section,
     idempotent under retry), decline (parked under `_declined/`,
     nothing destroyed).
   - Staging is now durable: the stories TTL reap keeps staged FILES
     and only expires the in-memory dry-run record. A `.planning`
     sentinel keeps the inbox off batches the planner is still
     writing.
   - `appendToCardBody` added to `fs/cards.ts` (atomic, publishes
     card-updated).
3. **Agent review caught two blocking filesystem bugs** before merge
   (manifest destroyed when its archive rename failed; duplicate
   absorb sections on merge retry). Fixed with regression tests.

Verification at merge: backend 91 tests (was 77), frontend 175 (was
160), both typechecks and the vite build clean.

## What is currently broken or incomplete

- Nothing failing.
- **Known follow-up (Drew's call):** `POST /api/stories/:batchId/cancel`
  still hard-deletes the staging dir. Cancel is an explicit discard so
  that is arguably intended, but it is now the only path that violates
  the inbox's nothing-destroyed guarantee. Option: park cancelled
  batches under `_declined/` like per-card decline.
- `/api/stories/pending` can list a batch the triage inbox already
  drained (its in-memory entry lives up to 1h); approve then 409s
  with a clear message. Cosmetic, reconcile whenever the submit
  surface gets touched next.
- The board's cost numbers still come from card frontmatter, not the
  agile-cards `card_metrics` ledger (which is now fully populated by
  chunk 2 + gate shadow data). The "switch cost source to the ledger"
  evaluation card from the sprint plan section 4 is still unwritten.

## What the next session should do first

1. Read this handoff, `docs/DASHBOARD_ROADMAP.md`, and
   `outputs/SPRINT_PLAN_2026-06-01_grid-and-next.md`.
2. **Next chunk: backlog grooming surface (roadmap 2.3)** -- the
   second half of Sprint 2's Option B. `/backlog` route, dense table,
   inline edit, bulk-select, Ready toggle. Pairs with 2.8
   (multi-select) if there is appetite.
3. Manual smoke worth two minutes: run the stack, submit a story with
   `STORIES_DEMO_INVOKER=1`, leave the dry-run unapproved, open
   /triage, promote one card and merge another into a similar
   existing card.

## Open questions for Drew

- Cancel semantics (above): hard-delete or park?
- Similarity threshold 0.34 and top-3 cap are heuristics; tune after
  real agent-generated volume arrives.

## Pointers

- Roadmap: `docs/DASHBOARD_ROADMAP.md` (2.4 shipped; 2.3 next)
- Sprint plan (living draft): `outputs/SPRINT_PLAN_2026-06-01_grid-and-next.md`
- Saved-views scope (now historical): `docs/SAVED_VIEWS_V0_SCOPE.md`

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then this
project's `CLAUDE.md` if present, then this file, then run `vstart`.
Note: `session-start.ps1` trips PowerShell 5.1's NativeCommandError
on `git fetch 2>&1` (fetch succeeds, script exits 1); run the
protocol steps manually if it fails.
