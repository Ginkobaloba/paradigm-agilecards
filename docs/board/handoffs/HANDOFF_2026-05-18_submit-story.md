# Submit Story Surface Handoff (2026-05-18)

Branch: `feature/submit-story-surface`
PR: opened against `main` of `agile-cards-board`.

## What shipped

The write side of the dashboard. Users can now paste a user story into
the dashboard, watch the `/cards` planner run, review a dry-run of the
proposed cards, and either approve them into the backlog or cancel.

The kanban (read side) already existed; this PR closes the loop so the
dashboard owns both ends of the story-to-card pipeline.

### Backend

- `POST /api/stories/submit` — accepts `{ story, project_path?, mode?,
  deep_planning?, timeout_ms? }`. Streams SSE while the planner runs.
  Stages output into `<CARDS_DIR>/_staging/<batchId>/`. Keeps the
  manifest in memory until approve or cancel.
- `POST /api/stories/:batchId/approve` — atomic promotion. Collision
  check first, then `fs.rename` every staged `.md` into
  `<CARDS_DIR>/backlog/` and the manifest into
  `<CARDS_DIR>/_batches/<batchId>/manifest.json`. Cleans up the
  staging dir on success.
- `POST /api/stories/:batchId/cancel` — discards staging.
- `GET /api/stories/pending` — list of in-memory pending batches. Used
  by the frontend to recover state on reload (future work).

All endpoints use the existing bearer-token middleware. Pending
batches age out of memory after one hour and the staging dir gets
cleaned at the same time. On backend restart, pending batches lose
their in-memory entry; the staging dir is left on disk so an operator
can promote or remove by hand.

### Frontend

- `/submit` route. Single-column page. Textarea for the story, project
  dropdown, advanced options for `deep_planning` toggle and mode
  override.
- Live progress panel showing each `[step] agent: message` event the
  planner emits.
- Dry-run panel with tier histogram, dependency count, claimable count,
  estimated tokens, and a per-card list.
- Approve flow: POST to `/approve`, wait briefly for chokidar to fire
  `card-added` events on the existing `/events` stream, navigate back
  to `/`.

State lives in a dedicated `useSubmitStore` slice (Zustand), distinct
from the cards store so the write-side lifecycle doesn't pollute the
read-side projection.

### Tests

- Backend: Node's built-in test runner via tsx, 4 cases covering
  validation, full submit+approve, cancel, and invoker error. Uses a
  fake invoker that writes a known manifest into the staging dir.
- Frontend: Vitest + jsdom + @testing-library/react, 4 cases covering
  route mount, textarea wiring, button enablement, project picker
  default. The SSE client is mocked.

## Decisions

### How the dashboard invokes `/cards`

Two paths considered:

1. **Shell out to the `claude` CLI in headless mode.**
2. **Call the Anthropic SDK directly with `SKILL.md` as the system prompt.**

We shipped (1). Reasoning:

- The CLI already implements the agent loop, tool-permission model,
  and MCP wiring that the skill depends on. Reimplementing that
  surface in the backend would duplicate code that already works.
- The skill is the canonical entry point Drew already uses from his
  terminal. The dashboard should be a different mouth on the same
  animal, not a parallel reimplementation.
- The runner design pass is in flight. When it lands a final
  invocation contract, replacing `claudeCliInvoker` is a one-file
  change — the rest of the route is agnostic.

The invoker is injectable. `storiesRouter({ invoker })` is the seam
that the backend tests use to substitute a fake, and is the same seam
the runner design will use when it lands its decision.

### Staging then promote

The `/cards` skill, as it exists today, writes cards directly to the
backlog. The submit-story surface needs a dry-run review *before*
anything lands. To avoid forking the skill, the dashboard:

1. Prepares an empty `<CARDS_DIR>/_staging/<batchId>/` directory.
2. Tells the skill (via prompt directive + `AGILE_CARDS_STAGING_DIR`
   env var) to write into that directory.
3. Reads the resulting `manifest.json` (or synthesizes one from `.md`
   frontmatter if the skill didn't write a manifest).
4. Holds the batch pending until the user approves.
5. On approval, does collision-checked `fs.rename` of every staged
   `.md` into `backlog/`.

The directive-in-the-prompt approach is the v1 contract with the
skill. The runner design pass is expected to land a first-class
`--output-root` flag or similar that the dashboard will swap to.
Until then, the directive plus env var is what we have.

### SSE on a POST

`EventSource` only does GET, but the submit body has a 64 KB story in
it. So we use `fetch()` with a streaming body reader and parse the SSE
wire format manually. Smallest viable thing; well-documented pattern
on MDN.

The original spec listed a `complete` event on the same stream after
approval. We deviated: the submit stream closes after `dry_run`, and
the approve POST returns JSON synchronously. The `card-added` events
that fire when the watcher sees the new files arrive on the existing
`/events` SSE channel, which the kanban already listens to. Holding
the submit stream open across an unknown human-review delay would tie
up a worker for no benefit.

### State on restart

Pending batches live in a process-local `Map`. If the backend restarts
between submit and approve, the `Map` is empty but the staging dir is
still on disk. The frontend's pending-recovery (read from
`GET /api/stories/pending`) will return an empty list. The operator
can either promote the staging dir by hand or delete it. Persistent
pending state is a future-work item; we opted not to add a sqlite
table for it in v1 since the recovery path is rare and benign.

## Known limits

- The `claude -p` invocation is the canonical entry point but its
  exact stdout format is still firming up. The invoker parses lines
  like `[planning] planner: ...` into `ProgressEvent`s and treats
  anything else as `info` chatter. The runner design pass may land a
  structured stdout protocol; if so, the parser becomes a thin
  adapter.
- The submit timeout is hard-capped at 30 minutes. A deep 3-agent
  plan on a large story should run in well under that.
- The pending batch TTL is one hour. If the operator walks away,
  comes back later, and tries to approve, the dashboard will 404. The
  staging dir is left on disk so they can promote by hand if needed.
- The frontend project dropdown is hard-coded for now. v2 should pull
  from `~/.cards-projects.yaml` or a backend-served list.

## Follow-ups

- Pull project list from the backend instead of a static dropdown.
- Persistent pending-batch state across restarts (sqlite table).
- "Recover pending" UI: on visiting `/submit`, fetch
  `GET /api/stories/pending` and offer to resume any in-flight batch.
- Show batch history (last N approved batches) on the kanban so users
  can correlate a card with the story that produced it.
- When the runner design pass lands a structured stdout protocol,
  replace the regex-based line parser with the structured one.

## Files added or changed

```
backend/src/stories/manifest.ts        (new)
backend/src/stories/staging.ts         (new)
backend/src/stories/invoker.ts         (new)
backend/src/routes/stories.ts          (new)
backend/src/routes/stories.test.ts     (new)
backend/src/server.ts                  (mounted route)
backend/package.json                   (added test script, esbuild dep)
frontend/src/lib/submitStory.ts        (new)
frontend/src/state/submitStore.ts      (new)
frontend/src/routes/SubmitStory.tsx    (new)
frontend/src/routes/SubmitStory.test.tsx (new)
frontend/src/App.tsx                   (added /submit route)
frontend/src/components/Header.tsx     (added Submit Story nav link)
frontend/package.json                  (added test deps + script)
frontend/vitest.config.ts              (new)
README.md                              (added "Submitting a story" section)
```

## Verification

```
cd backend ; npm run typecheck ; npm test
cd frontend ; npm run typecheck ; npm test
```

Both packages pass `tsc --noEmit` and their respective test runner
cleanly on `feature/submit-story-surface`.
