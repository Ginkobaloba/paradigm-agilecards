# Recovery + Timeline Handoff (2026-05-22)

Session-opening assumption (per dispatch context): "4 stacked feature PRs
(#8-#11) plus a roadmap doc PR." Actual on-main reality at session start:
only PRs #7 (roadmap) and #8 (cost surfaces) had landed. PRs #9, #10, #11
were marked "MERGED" by GitHub because each merged into its stack-base,
but those base branches never made it onto `main`, leaving the feature
commits (`e49db27` tile, `16894bb` rank, `ffb9940` cmdk) dangling.

This session recovered all three onto fresh branches off current `main`
and chained one new horizon-2 roadmap feature (item 2.5 -- per-card live
event timeline) on top of the recovered stack.

## Next Session Onboarding

Before doing anything else, the next session must:

1. Read `C:\dev\SESSION_PROTOCOL.md`.
2. Read this project's `CLAUDE.md` if present.
3. Read this handoff doc end-to-end.
4. Run `vstart` (or `C:\dev\_scripts\session-start.ps1`).

## Open PR stack at session end

All four are open, stacked, awaiting Drew's review and merge. None were
merged by this session per instruction.

| PR | Branch | Base | Title | Roadmap items |
|----|--------|------|-------|---------------|
| [#12](https://github.com/Ginkobaloba/agile-cards-board/pull/12) | `feature/tile-polish-v2` | `main` | feat(tile): copy-id, age, blocked-on dep badge (re-land of #9) | 1.7 |
| [#13](https://github.com/Ginkobaloba/agile-cards-board/pull/13) | `feature/manual-rank-v2` | `feature/tile-polish-v2` | feat(rank): drag-to-reorder within a column + sort dropdown (re-land of #10) | 1.4 |
| [#14](https://github.com/Ginkobaloba/agile-cards-board/pull/14) | `feature/cmdk-filter-views-v2` | `feature/manual-rank-v2` | feat(nav): Cmd-K palette, filter chips, saved views, keyboard (re-land of #11) | 1.1, 1.2, 1.5, 1.6 |
| [#15](https://github.com/Ginkobaloba/agile-cards-board/pull/15) | `feature/card-event-timeline` | `feature/cmdk-filter-views-v2` | feat(timeline): per-card lifecycle event stream in card detail | 2.5 |

Recommended merge order: 12 → 13 → 14 → 15. GitHub should auto-retarget
each upstream PR to `main` as the lower stack member merges (the same
mechanism that misled us the first time around, but applied properly).

Untouched per instruction: [#1](https://github.com/Ginkobaloba/agile-cards-board/pull/1)
(Dependabot Vite 5 -> 8 major bump). Major-version bump, separate call.

## Why the original #9-#11 went stranded

Stacked-PR pattern. Each upstream PR's base branch was the next PR down
the stack. When Drew merged #8 (cost-surfaces -> main), the merge commit
landed on `main`, but GitHub re-targeted #9-#11's bases to the squash-or-
merge target without bringing along the upstream commits as new commits
on main. Each upstream PR's head SHA appeared in its base branch's
history (because of the stacked merges between them), so GitHub flipped
them to "MERGED" -- but the work itself wasn't on main.

How I diagnosed:

```
git log --oneline origin/main  # only had cost-surfaces, not the upstream
git log --oneline origin/main..origin/feature/cmdk-filter-views
# returned several non-empty commits, including ffb9940 (cmdk feature)
gh pr view 9 --json baseRefName  # base was feature/cost-surfaces, not main
```

The dangling branches still existed on the remote, so recovery was a
cherry-pick of the three feature commits onto fresh branches off
current `main`.

## What this session built (PR #15: per-card timeline)

Roadmap item 2.5. A collapsible Timeline panel in the card detail modal
shows the runner lifecycle for that card (`discovered`, `started`,
`heartbeat`, `finished`, `verifier_called`, `cascade`, `released`,
`merge_status_changed`, `status_changed`). Persisted in a new schema-v4
SQLite table, patched live via SSE.

Design highlights:

- **Derive events from frontmatter deltas** (`backend/src/events/derive.ts`)
  rather than asking the runner to emit them. Keeps the runner contract
  simple. When the runner eventually publishes a structured event stream,
  this module becomes a thin adapter.
- **Heartbeat collapsing**: `lib/timelineGroup.ts` reduces adjacent
  heartbeats to a single "N heartbeats" row so a chatty runner doesn't
  dominate the visible timeline.
- **State in the component, not the store**: a tiny typed pub/sub
  (`lib/cardEventBus.ts`) delivers live events. The Timeline subscribes
  on mount, unsubscribes on unmount. We don't pay store memory for a
  feature that's invisible 90% of the time.
- **Bootstrap one-shot backfill**: at first-ever startup, every card on
  disk gets a synthesized `discovered` event so the timeline isn't empty.
  Idempotent across restarts via `countEventsForCard`.

Test totals after PR #15: backend **57 pass** (21 new), frontend
**62 pass** (4 new). Build clean.

## Sequence of operations this session

1. Reconciled PR state against `gh pr list` and `git log origin/main`.
   Caught the strand-mismatch and surfaced to Drew before doing any
   recovery work.
2. Cherry-picked `e49db27` onto a fresh branch off current main ->
   `feature/tile-polish-v2`. Verified backend + frontend typecheck /
   test / build. Opened PR #12.
3. Branched `feature/manual-rank-v2` from #12's branch, cherry-picked
   `16894bb`. Verified. PR #13.
4. Branched `feature/cmdk-filter-views-v2` from #13's branch,
   cherry-picked `ffb9940`. Verified. PR #14. (Skipped the original
   handoff-doc cherry-pick `0209b4b` because its content referenced the
   now-stale PR numbers.)
5. Branched `feature/card-event-timeline` from #14's branch and built
   roadmap item 2.5 end-to-end.

## Decisions in PR #15

- **Schema v4 is additive.** No data motion, no rewrites of existing
  tables. Existing dashboards upgrade-in-place by running the migration
  on next boot.
- **Details column is opaque JSON.** Backend doesn't query into the
  details; the frontend decides what to render. Keeps the schema fixed
  even as new event types ship.
- **Timeline lives above the frontmatter table** in the card modal.
  Below the title, above the frontmatter table, above the body. This
  is the order an operator scans the modal in: what's happening, then
  metadata, then the brief.
- **Cardinality control: no retention policy yet.** The table grows
  monotonically. Easy to add a TTL sweep later; deferred until it
  proves to be a problem (a year of busy runners with ~5 events/card
  is still well under SQLite scale).
- **No replay-from-disk for historical phases.** Events captured from
  the moment this feature lands forward. Older cards get a
  `discovered` baseline only. We could backfill `started` /
  `finished` from existing frontmatter timestamps, but the
  bootstrap path is intentionally minimal -- if the operator wants
  full history retroactively, they can wipe `card_events` and re-add
  a richer backfill in a follow-up.

## Verification of PR #15 against current main

From `feature/card-event-timeline`:

- `npm --prefix backend run typecheck` -- clean
- `npm --prefix backend test` -- 57 pass, 8 suites
- `npm --prefix frontend run typecheck` -- clean
- `npm --prefix frontend test` -- 62 pass, 7 suites
- `npm --prefix frontend run build` -- clean

The recovery branches were verified independently as well:

- PR #12 (tile-polish-v2): backend 14, frontend 33, build clean
- PR #13 (manual-rank-v2): backend 28, frontend 41, build clean
- PR #14 (cmdk-filter-views-v2): backend 36, frontend 58, build clean

## Risks for Drew's review

- **Manual smoke for PR #15**: I have no live runner in the Dispatch
  sandbox, so the end-to-end "edit a card on disk, see the heartbeat
  appear in the modal within ~1s" was not exercised. Worth a 2-minute
  manual: open a card detail, then touch the file (`Set-Content
  $cardPath (Get-Content $cardPath)`), and confirm the SSE delivers a
  new event.
- **Schema v4 migration on the production DB**: additive, idempotent,
  but the live DB has never seen this migration. First boot after
  merge will run it and create `card_events`. No backup needed because
  no existing data is touched.
- **Bootstrap backfill emits one `discovered` per card.** If the live
  DB already has a lot of cards (~100s), this is a one-shot insert
  storm on first boot. Within a single transaction it should complete
  in <1s, but watch the logs for that startup.
- **The original `feature/tile-polish`, `feature/manual-rank`, and
  `feature/cmdk-filter-views` branches still exist on the remote** in
  their funky multi-merge state. Safe to delete after the recovery
  PRs land -- I left them alone so Drew can verify the recovery first.

## What's next from the roadmap

PR #15 closes 2.5. From section 9 of `docs/DASHBOARD_ROADMAP.md`
("first-quarter cut"), what remains in priority order:

1. **2.1 sprint planner UI (L)** -- biggest payoff after cost. Backend
   already speaks `/api/sprints` and `POST /api/sprints/:id/cards`.
   Probably wants to be 2-3 PRs of its own.
2. **2.2 capacity model (M)** -- stoplight on sprint header for the
   binding constraint (agents / $ / review bandwidth). Only useful with
   2.1 done.
3. **2.6 dependency view (M)** -- DAG renderer for the `depends_on`
   field. The `selectUnmetDeps` selector from PR #12 is already the
   read-side. Self-contained.
4. **2.11 per-project lens (S)** -- group-by-project toggle on the
   kanban. Tiny.

Open questions Drew flagged in section 10 of the roadmap (cost-cap
default, review-bandwidth number, agent avatars, embeddings provider,
multi-tenant timing) are unblocked but not blocking.

## Files changed

### Recovery PRs (#12, #13, #14)

Identical diffs to original PRs #9, #10, #11 respectively. See those PRs'
file lists for details.

### PR #15 (timeline)

```
backend/
  package.json                            (added test files to test script)
  src/db/sqlite.ts                        (schema v4)
  src/db/events.ts                        (new)
  src/db/events.test.ts                   (new, 7 tests)
  src/events/bus.ts                       (added card-event-added)
  src/events/derive.ts                    (new)
  src/events/derive.test.ts               (new, 14 tests)
  src/fs/cards.ts                         (derive+persist+publish hook,
                                           bootstrap backfill)
  src/routes/events.ts                    (new)
  src/server.ts                           (mount)
frontend/
  src/components/CardModal.tsx            (renders Timeline)
  src/components/Timeline.tsx             (new)
  src/hooks/useSSE.ts                     (card-event-added handling)
  src/lib/api.ts                          (CardEventRow + listCardEvents)
  src/lib/cardEventBus.ts                 (new typed pub/sub)
  src/lib/timelineGroup.ts                (new)
  src/lib/timelineGroup.test.ts           (new, 4 tests)
```

## Pointers

- Roadmap: `docs/DASHBOARD_ROADMAP.md` (sections 4-5 for horizons 2-3)
- Prior session handoffs:
  - `docs/handoffs/HANDOFF_2026-05-17_dashboard-v0plus.md` (initial scaffold)
  - `docs/handoffs/HANDOFF_2026-05-18_submit-story.md` (write side)
- Earlier near-term roadmap handoff (now stale, lived on
  `feature/cmdk-filter-views` and was intentionally NOT cherry-picked
  into PR #14): describes what was built in the original #8-#11 stack
  before the strand-mismatch.
