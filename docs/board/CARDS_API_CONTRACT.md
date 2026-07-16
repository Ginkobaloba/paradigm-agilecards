# Cards API Contract (v1)

**Status:** authoritative implementation spec for `backend/cards_api/`.
**Derived from:** the legacy Express backend (`legacy/board-express/backend/src`)
and the frontend consumers (`frontend/src/lib/api.ts`, `lib/submitStory.ts`,
`hooks/useSSE.ts`), extracted 2026-07-16. The wire contract below is what the
existing frontend already speaks; the FastAPI backend implements it over
Postgres so the frontend cutover is a config + auth-token change, not a
rewrite. Deviations from legacy are listed at the end and are intentional.

Conventions:

- All errors are `{ "error": string }` (some validation errors add
  `valid: string[]`). The frontend reads `payload.error`.
- Wire casing is camelCase for cards/ranks/views/sprints, snake_case for
  retros and the stories submit stream (legacy quirk, preserved).
- Auth: `Authorization: Bearer <Paradigm RS256 JWT>` on every `/api/*` route.
  `GET /events` may authenticate via `?token=<jwt>` because `EventSource`
  cannot set headers. No token -> `401 {"error":"missing_token"}`; bad token
  -> `401` with a stable reason code. Insufficient role -> `403`.
- Org scoping: every resource belongs to the `org_id` of the verified token.
  Cross-org reads answer **404, not 403** (no id probing).

## Health

`GET /healthz` (public) -> `200 {"ok": true, "version": string, "db": "ok"|"error"}`.
`ok` is `true` iff the process is up; `db` reports a best-effort connectivity
probe. (Legacy returned `{ok, cardsDir, version}`; `cardsDir` is meaningless
without a filesystem store and is dropped. The smoke gate must assert
`$.ok == true`.)

## Identity

`GET /api/me` -> `200 {"sub": string, "org_id": string, "roles": string[]}`.

## Columns

`GET /api/columns` -> `200 {"columns": [{id, label}]}` — fixed five, in order:
`backlog`/Backlog, `active`/Active, `awaiting_amendment_review`/In Review,
`done`/Done, `blocked`/Blocked.

## Cards

Card wire shapes:

```ts
CardSummary = { id: string, file: string, status: StatusId,
                frontmatter: Record<string, unknown>, mtimeMs: number }
CardDetail  = CardSummary & { body: string }
```

`file` is the card's logical filename (`<name>.md`, unique per org).
`mtimeMs` is the float epoch-ms of the last modification (`updated_at`).

- `GET /api/cards` -> `200 {"cards": CardSummary[]}` sorted by column order
  then `id`.
- `GET /api/cards/{id}` -> `200 CardDetail` | `404 {"error":"no such card"}`.
- `POST /api/cards` (role: `admin`) body
  `{title: string, status?: StatusId, frontmatter?: object, body?: string}`
  -> `201 CardDetail`. Direct create path (legacy had none; cards arrived via
  stories/triage). `org_id` always comes from the token, never the body.
- `POST /api/cards/{id}/move` body `{status: StatusId}` ->
  `200 {id, file, status, rank}`. Invalid status ->
  `400 {"error":"status must be one of", "valid": [...]}`. Cross-column moves
  drop the old rank and append at the bottom of the target column
  (max rank + 1024, or 1024 if empty). Publishes SSE `card-state-changed`.
- `PATCH /api/cards/{id}/frontmatter` — whitelisted scalar patch. Any key
  outside the whitelist -> `400 {"error":"field not patchable: <key>"}`;
  empty patch -> `400 {"error":"empty patch"}`; non-object body -> `400`.

  | field | accepted | notes |
  |---|---|---|
  | `stakes` | `"low"|"medium"|"high"` or `null` | null deletes the key |
  | `cost_cap_usd` | finite number > 0, or `null` | |
  | `title` | non-empty string | stored trimmed |
  | `points` | integer 1..6 | |
  | `ready` | boolean or `null` | null clears |

  Success -> `200 CardSummary`. Publishes `card-updated` and runs event
  derivation.

## Ranks

Float midpoint ranking, `RANK_BASE = RANK_STEP = 1024`.

- `GET /api/ranks` -> `200 {"ranks": [{cardId, status, rank}]}`.
- `POST /api/cards/{id}/rank` body `{status, prevId: string|null,
  nextId: string|null}` -> `200 {cardId, status, rank}`. Neighbor ranks are
  looked up server-side (client rank values are never trusted):
  both -> midpoint; prev only -> prev+1024; next only -> next-1024;
  neither -> column max + 1024 (or 1024). Upsert keyed by card. No automatic
  rebalance (float precision degrades only after ~2^53 same-spot inserts).

## Card events

- `GET /api/cards/{id}/events?limit=&since=` -> `200 {"events":
  [{id: number, cardId, type, at: ISO string, details}]}` ordered `id ASC`.
  `limit` clamped 1..1000 (default 500); `since` filters `at > since`.

Derived event `type` values (computed by diffing frontmatter on every write,
same rules as legacy `events/derive.ts`): `discovered`, `status_changed`,
`started`, `released`, `heartbeat`, `finished`, `verifier_called`, `cascade`,
`merge_status_changed`. Each also publishes SSE `card-event-added`.

## Rates

`GET /api/rates` -> `200 {"rates": ModelRate[], "defaultInputRatio": 0.6}`,
`ModelRate = {model, inputPerMTokens, outputPerMTokens, displayName?}`.
Static table, same five entries as legacy.

## Saved views

Scoped to the caller (`org_id` + token `sub`); legacy scoped by opaque token
id. Wire shape keeps the `tokenId` field for compatibility; it is always `0`.

```ts
SavedView = { id: number, tokenId: number, name: string, payload: unknown,
              createdAt: string, updatedAt: string }
```

- `GET /api/views` -> `200 {"views": SavedView[]}` ordered by name.
- `POST /api/views` `{name, payload}` -> `201 SavedView`. Name non-empty
  <= 80 chars; payload JSON-serializable <= 16384 bytes. Duplicate name ->
  `409`.
- `PATCH /api/views/{id}` `{name?, payload?}` -> `200 SavedView` | `404`.
- `DELETE /api/views/{id}` -> `204` | `404 {"error":"no such view"}`.
- `GET /api/views/{id}` -> `200 SavedView` | `404` (parity convenience).

## Sprints

```ts
Sprint = { id, name, startsAt, endsAt, goal: string|null, status,
           pointsTarget: number|null, dollarTarget: number|null,
           reviewHoursTarget: number|null, archivedAt: string|null, createdAt }
SprintStatus = "planning"|"active"|"completed"|"cancelled"
```

- `GET /api/sprints?includeArchived=1` -> `200 {"sprints": (Sprint &
  {cardCount, plannedPointsSum})[]}` ordered `startsAt DESC`; archived
  excluded by default.
- `POST /api/sprints` `{name, startsAt, endsAt, goal?, status?}` ->
  `201 {"sprint": Sprint}`. `endsAt < startsAt` -> `400`.
- `GET /api/sprints/{id}` -> `200 {"sprint": Sprint, "cards":
  [{sprintId, cardId, plannedPoints}]}`.
- `PATCH /api/sprints/{id}` — any subset of the Sprint fields;
  `pointsTarget` floored >= 0; unknown-only body ->
  `400 {"error":"no recognized fields in body"}`. -> `200 {"sprint": Sprint}`.
- `POST /api/sprints/{id}/cards` `{cardId, plannedPoints}` -> `204` (upsert).
- `DELETE /api/sprints/{id}/cards/{cardId}` -> `204` (idempotent).

## Retros

Raw snake_case wire shape (legacy quirk, preserved):
`RetroRow = {id, sprint_id: number|null, held_on, summary: string|null,
created_at}`.

- `GET /api/retros` -> `200 {"retros": RetroRow[]}` ordered `held_on DESC`.
- `POST /api/retros` `{sprintId?, heldOn, summary?}` -> `201 {"id": number}`.
- `GET /api/retros/{id}` -> `200 RetroRow` | `404`.

## Triage

Staged batches of proposed cards awaiting promote/decline/merge. In the new
backend batches live in Postgres (not `_staging/` dirs); they are ingested via
the service endpoint below.

```ts
TriageBatch = { batchId, story: string|null, cards: TriageCard[] }
TriageCard  = { id, title, file, bodyExcerpt /*<=280 chars*/,
                tier: number|null, model: string|null,
                estimatedTokens: number|null, dependsOn: string[] }
```

- `GET /api/triage` -> `200 {"batches": TriageBatch[]}` — batches with at
  least one staged card, sorted by batchId; cards sorted by id. Batches still
  `planning` are omitted.
- `POST /api/triage/{batchId}/cards/{file}/promote` ->
  `200 {id, status: "backlog", rank}`. Card becomes a real backlog card with
  an appended rank. `409` if a card with the same id/file already exists.
- `POST /api/triage/{batchId}/cards/{file}/decline` -> `200 {"ok": true}`.
  Non-destructive (card retained, state `declined`).
- `POST /api/triage/{batchId}/cards/{file}/merge` `{targetId}` ->
  `200 {"ok": true, targetId}`. Appends the staged body to the target card
  under an `## Absorbed from triage (<id>)` marker; idempotent under retry;
  then declines the staged card. Unknown target -> `404`.
- `POST /api/triage/batches` (role: `service` or `admin`) — **new endpoint**,
  replaces "the engine writes files into `_staging/`". Body
  `{batchId, story?, cards: [{file, title?, frontmatter?, body?}]}` ->
  `201 {"batchId": ..., "cardCount": n}`. `409` on duplicate batchId.

## SSE

`GET /events?token=<jwt>` -> `text/event-stream`. Wire format per event:
`event: <type>\n` + `data: <JSON of the full event object incl. type>\n\n`.
One `heartbeat` on connect, then `{type:"heartbeat"}` every 25 s. Events are
org-scoped — a connection only receives its own org's events.

| event | payload |
|---|---|
| `heartbeat` | `{type}` |
| `card-added` | `{type, cardId, status}` |
| `card-updated` | `{type, cardId, status}` |
| `card-removed` | `{type, cardId}` |
| `card-state-changed` | `{type, cardId, status}` |
| `card-event-added` | `{type, cardId, event: {id, cardId, type, at, details}}` |

The frontend refetches full card state on change events; SSE payloads
deliberately do not carry frontmatter.

## Audit (new)

- `GET /api/audit?limit=&since=` (role: `admin`) -> `200 {"events":
  [{id, at, actorSub, action, resourceType, resourceId, outcome, details}]}`.
  Org-scoped read over the append-only audit log (see the ADR).

## Deviations from legacy (intentional)

| # | Deviation | Reasoning |
|---|---|---|
| 1 | Auth is Paradigm JWKS RS256 only; no opaque local tokens, no portal-JWT fallback. | K11 auth contract is the platform standard; opaque tokens were pre-Paradigm. |
| 2 | `/healthz` returns `{ok, version, db}` — `cardsDir` dropped. | No filesystem store. Smoke gate asserts `$.ok == true` (fixes audit M3). |
| 3 | Saved views scoped by `org_id` + `sub`; `tokenId` pinned to `0`. | Token ids were an opaque-token concept. Field kept so the frontend type still parses. |
| 4 | `POST /api/cards` exists (admin). | Direct create seam; legacy only created cards via stories/triage. |
| 5 | `POST /api/triage/batches` exists (service/admin). | DB-native replacement for engine-writes-to-`_staging/`. |
| 6 | Stories routes (`/api/stories/submit|approve|cancel`) are **not implemented** and return 501. | They shell out to the `claude` CLI — that is the execution-engine integration (audit P2, "unify execution paths behind the runner"). Deferred deliberately; see the ADR. |
| 7 | `card_events.since` must parse as a timestamp; unparseable -> `400 {"error":"invalid since"}`. | Legacy did raw string comparison against TEXT storage. |
| 8 | Cross-org access anywhere -> 404, enforced by Postgres RLS *and* app-layer scoping. | Audit S2: isolation must be DB-enforced, not a list comprehension. |
| 9 | All mutations and auth denials append to an immutable `audit_events` table. | Audit S3 compliance seam. |
