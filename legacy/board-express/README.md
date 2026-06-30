# agile-cards-board

Web dashboard for the [`agile-cards`](https://github.com/Ginkobaloba/agile-cards)
skill. A drag-drop kanban over a filesystem-backed card store, with sprint
planning and retros wired in for later.

This is the production-grade companion to the single-file `dashboard/v0/index.html`
prototype that ships inside the parent repo. The v0 page is great for a quick
glance; this one is the daily driver.

## What it is

The `/cards` skill stores work-tracking cards as markdown files on disk under
a `todo/` tree, one file per card, with a status frontmatter field and a
subfolder per status:

```
todo/
  backlog/        -- cards waiting to be claimed
  active/         -- cards an executor is working
  amendments/     -- cards awaiting human review (status: awaiting_amendment_review)
  done/           -- cards whose work merged
  blocked/        -- cards finished but unmerged, or paused on deps
```

This dashboard reads that tree, watches it live, and gives you a real UI to
move cards between columns, inspect their frontmatter and body, and (soon)
plan sprints from the backlog and run retros against the done column.

## Stack

```
backend/    Express + TypeScript + better-sqlite3 + chokidar + SSE
frontend/   React + Vite + TypeScript + Tailwind + Radix primitives + @dnd-kit + Zustand
```

The card files stay on disk as the source of truth. SQLite holds dashboard
state that doesn't belong in a card: auth tokens, sprint scheduling, retro
history, per-user preferences. If you blow away the SQLite database you lose
the dashboard's own state, but every card is still safe in `todo/`.

## Running locally

You need Node 20+ and npm. Docker is optional; useful for the
all-in-one production-style run.

### Without Docker (dev loop)

```powershell
# 1) Install
cd C:\dev\agile-cards\apps\board\backend
npm install

cd ..\frontend
npm install

# 2) Point the backend at your card store. Default is C:\dev\todo.
$env:CARDS_DIR = "C:\dev\todo"
$env:DB_PATH   = "C:\dev\agile-cards\apps\board\backend\data\board.sqlite"
$env:PORT      = "4070"

# 3) Mint your first auth token
cd ..\backend
npm run create-token -- --label "drew-laptop"
# prints the token. Save it. The backend stores only the hash.

# 4) Run backend
npm run dev
# listens on http://localhost:4070

# 5) In a second terminal, run the frontend dev server
cd ..\frontend
npm run dev
# Vite serves http://localhost:5173 and proxies /api + /events to the backend.
```

Open `http://localhost:5173`, paste your token when prompted, you're in.

### With Docker (single-host production-style)

```powershell
cd C:\dev\agile-cards\apps\board
docker compose up --build
```

That brings up:

- `backend` on port 4070, mounting `C:\dev\todo` read-write and persisting
  SQLite to a named volume.
- `frontend` served as static files by nginx on port 8080, proxying
  `/api` and `/events` through to the backend.

Tokens are created against the running backend container:

```powershell
docker compose exec backend node dist/scripts/create-token.js --label "drew-laptop"
```

## Configuration

Environment variables read by the backend at startup:

| var          | default                                | what it does                                     |
|--------------|----------------------------------------|--------------------------------------------------|
| `PORT`       | `4070`                                 | HTTP port the backend binds                      |
| `CARDS_DIR`  | `C:\dev\todo`                          | Where the card files live                        |
| `DB_PATH`    | `./data/board.sqlite`                  | SQLite database file                             |
| `CORS_ORIGIN`| `http://localhost:5173`                | Allowed origin(s) for browser calls. Comma-separated list. |
| `LOG_LEVEL`  | `info`                                 | `error`, `warn`, `info`, `debug`                 |
| `CLAUDE_CLI_PATH` | `claude`                          | Override the `claude` CLI binary path for the planner invoker |
| `PORTAL_JWKS_URL` | (unset)                           | Paradigm portal JWKS endpoint. When set with `PORTAL_ISSUER`, the board also accepts portal-minted RS256 JWTs as bearer credentials (the "Gantry" embed). |
| `PORTAL_ISSUER`   | (unset)                           | Exact portal origin the JWT `iss` must match (e.g. `https://portal.paradigm.codes`). Federation is off unless both this and `PORTAL_JWKS_URL` are set. |
| `PORTAL_AUDIENCE` | `gantry`                          | The app-slug the JWT `aud` must match. |

Frontend build-time variables:

| var               | default | what it does                                              |
|-------------------|---------|-----------------------------------------------------------|
| `VITE_BASE_PATH`  | `/`     | URL prefix the app is served from. Set to `/board/` (or `/gantry/`) to host this stack behind the Paradigm portal reverse proxy. Bakes into both Vite's `base` (asset URLs + React Router `basename`) and the nginx location prefixes inside the docker image. |
| `VITE_APP_BRAND`  | `agile-cards` | Customer-facing brand shown in the header and document title. The portal embed builds with `Gantry`. |
| `VITE_APP_TAGLINE`| `board v0+` | Small badge next to the brand. |

## Hosting behind the Paradigm portal

The Paradigm portal at `portal.projectnexuscode.org` reverse-proxies
`/board/` through to this app. To build an image that serves correctly
under that prefix:

```powershell
$env:BASE_PATH = "/board/"
$env:CORS_ORIGIN = "https://portal.projectnexuscode.org"
docker compose build
docker compose up -d
```

The trailing slash on `BASE_PATH` matters. The frontend container will
serve at `/board/`, expect API calls at `/board/api/*`, SSE at
`/board/events`, and health at `/board/healthz`. Cloudflare Access on
`*.projectnexuscode.org` enforces identity at the edge; the portal's
reverse-proxy rule routes `/board/` to this stack's frontend container
without rewriting the path.

For local dev under a base path:

```powershell
$env:VITE_BASE_PATH = "/board/"
cd frontend
npm run dev
# open http://localhost:5173/board/
```

The Vite dev proxy strips the `/board` prefix off `/api`, `/events`,
and `/healthz` before forwarding to the backend, so the backend keeps
its single-mount-point routes.

## Gantry: the portal embed at `/gantry/`

The board ships to customers as **Gantry**, a tile inside the Paradigm
portal (`portal-shell`). A logged-in portal user clicks the Gantry tile,
the portal mints a short-lived RS256 JWT and hands it off in the URL
fragment, and the board verifies it and shows the kanban -- no second
login.

This is one concrete instance of the generic "host behind the portal"
support above. What makes it Gantry rather than a bare `/board/` mount:

1. **Base path `/gantry/`** -- the image is built with `BASE_PATH=/gantry/`.
2. **Brand** -- built with `APP_BRAND=Gantry`, so the header and tab title
   read Gantry, not `agile-cards`.
3. **Portal federation** -- the backend trusts portal-minted JWTs.

### Auth: portal JWKS federation

The board honors the [portal Gate Contract](../../../portal-shell/docs/PORTAL_GATE_CONTRACT.md),
the same handoff the demo fleet uses:

- The portal's launch route redirects to `<board>/#portal_token=<JWT>`.
- `frontend/src/lib/portalHandoff.ts` reads the fragment on boot, stores
  the JWT as the board's bearer token (the existing sessionStorage slot),
  and scrubs the fragment so it cannot leak or replay.
- `backend/src/auth/portalToken.ts` verifies the JWT against the portal
  JWKS (`PORTAL_JWKS_URL`), checking `iss` (`PORTAL_ISSUER`), `aud`
  (`PORTAL_AUDIENCE`, default `gantry`), and expiry. The local SQLite
  bearer tokens still work; the JWT path is a fallback tried only for
  JWT-shaped credentials when federation is configured.

Federation is inert unless both `PORTAL_JWKS_URL` and `PORTAL_ISSUER` are
set, so a standalone deployment is unaffected.

### Running the Gantry overlay

`docker-compose.gantry.yml` overlays the base compose with the Gantry
brand, base path, federation env, and a distinct host port (8110) the
portal's reverse proxy reaches via `host.docker.internal:8110`:

```powershell
$env:BASE_PATH   = "/gantry/"
$env:CORS_ORIGIN = "https://portal.projectnexuscode.org,https://portal.paradigm.codes"
docker compose -f docker-compose.yml -f docker-compose.gantry.yml up -d --build backend frontend
```

Only `backend` and `frontend` come up; the public ingress is the portal's
shared `demo-proxy` nginx, not this stack's own `cloudflared`.

### Note on the Docker base-path path

Two fixes landed with the Gantry work because the image had never been
run under a non-root base path before:

- The backend entrypoint is `dist/src/server.js` (tsc preserves the
  `src/` + `scripts/` tree under `rootDir: "."`), not `dist/server.js`.
- The frontend build is copied **under** `BASE_PATH` in the image
  (`/usr/share/nginx/html/gantry/`), because Vite prefixes asset URLs and
  the SPA fallback with the base path.

## Submitting a story

The kanban is the read side of the system. The submit-story surface at
`/submit` is the write side.

1. Open `/submit` from the header. Paste a user story in the textarea.
2. Pick a project from the dropdown, or leave it as "(no project)".
3. Optionally open "Advanced options" to force deep (3-agent) planning
   or override the project's mode.
4. Click **Plan this story**. The backend invokes the `/cards` skill in
   a staging directory under `<CARDS_DIR>/_staging/<batchId>/`. Progress
   events stream back over SSE so you can watch the planner and reviewer
   work in real time.
5. When planning finishes, a dry-run review panel appears with the
   proposed cards, tier histogram, depends-on edges, and total estimated
   tokens. Nothing is in the backlog yet.
6. Click **Approve and write to backlog** to promote the staged batch.
   The cards land in `<CARDS_DIR>/backlog/`, the manifest moves to
   `<CARDS_DIR>/_batches/<batchId>/manifest.json`, the chokidar watcher
   fires `card-added` events, and the kanban updates live.
7. Click **Cancel** to discard the staging dir and start over.

The submit endpoint and its companions are bearer-token-gated, same as
the rest of `/api/*`:

- `POST /api/stories/submit` — streams `progress`, `dry_run`, and
  `error` SSE events.
- `POST /api/stories/:batchId/approve` — promotes to backlog.
- `POST /api/stories/:batchId/cancel` — discards the staging dir.
- `GET  /api/stories/pending` — lists in-memory pending batches.

The planner runs as a subprocess (`claude -p` by default). The wire-up
between the dashboard and the runner is documented in
`docs/handoffs/HANDOFF_2026-05-18_submit-story.md`.

## Auth

Bearer tokens, the same scheme as Sprint 3b on brainstem, but with a
separate token store on the dashboard backend for now. No cross-service
state. Token storage: SHA-256 hash + a public label + created/last-used
timestamps. Plaintext is shown exactly once at creation; lose it and you
mint another one.

```powershell
# create
npm run create-token -- --label "iphone"

# list (no plaintext, just labels and timestamps)
npm run list-tokens

# revoke
npm run revoke-token -- --label "iphone"
```

Every request to `/api/*` and `/events` must carry `Authorization: Bearer <token>`.
The frontend stores the token in `sessionStorage` so a tab reload keeps you
signed in but closing the tab logs you out. That can be loosened to
`localStorage` later if you want long-lived browser sessions.

## Real-time

The backend watches `CARDS_DIR` with chokidar. When a card file is added,
modified, moved, or removed, the backend rescans the affected file, updates
its in-memory index, and pushes a `card-state-changed` SSE event to every
connected dashboard. The frontend's `useSSE` hook applies the diff in-place;
no full refresh.

That means you can have the dashboard open in two tabs, drag a card in one,
and the other reflects the move within a few hundred milliseconds. It also
means edits made by `/cards` runners on disk show up live.

## Hosting

Long-term home is `projectNexusCode.org` via a Cloudflare tunnel pointing
at `BROOKFIELD_PC:4070`. The tunnel is wired up in a separate step; this
repo just expects to run on whatever port `PORT` says.

## Project layout

```
agile-cards-board/
  LICENSE                    PolyForm Noncommercial 1.0.0
  README.md                  this file
  docker-compose.yml         backend + nginx static-file server
  docker/
    Dockerfile.backend
    Dockerfile.frontend
    nginx.conf
  backend/
    package.json
    tsconfig.json
    src/
      server.ts              Express app entry
      config.ts              env parsing, single source of truth
      logger.ts              tiny pino-style logger
      routes/
        cards.ts             GET /api/cards, POST /api/cards/:id/move, GET /api/cards/:id
        sprints.ts           GET/POST /api/sprints (stubbed)
        retros.ts            GET/POST /api/retros (stubbed)
        sse.ts               GET /events
        auth.ts              middleware
      db/
        sqlite.ts            better-sqlite3 setup, schema migrations
      fs/
        cards.ts             chokidar watcher, YAML parsing, atomic move
        frontmatter.ts       parser
      auth/
        tokens.ts            store + validate + mint + revoke
        hash.ts              SHA-256 helper
      events/
        bus.ts               in-process pub/sub for SSE
    scripts/
      create-token.ts
      list-tokens.ts
      revoke-token.ts
  frontend/
    package.json
    tsconfig.json
    vite.config.ts
    tailwind.config.ts
    postcss.config.cjs
    index.html
    src/
      main.tsx
      App.tsx
      routes/
        Kanban.tsx
        SprintPlanner.tsx     (v1 stub)
        Retros.tsx            (v1 stub)
        Login.tsx
      components/
        CardTile.tsx
        Column.tsx
        CardModal.tsx
        Header.tsx
        TokenGate.tsx
      hooks/
        useCards.ts
        useSSE.ts
        useAuth.ts
      lib/
        api.ts
        parseCard.ts
        tierBadge.ts
      state/
        store.ts              Zustand store
      styles/
        globals.css
  docs/
    handoffs/
      HANDOFF_2026-05-17_dashboard-v0plus.md
```

## License

PolyForm Noncommercial 1.0.0. Same as the parent `agile-cards` repo.
Use it however you want for personal, research, or noncommercial work.
Commercial use needs a separate arrangement.

## Status

- [x] Backend: cards REST, SSE, auth, chokidar live updates, atomic move
- [x] Frontend: kanban with drag-drop, card modal, login gate, live updates
- [ ] Sprint planner UI (placeholder)
- [ ] Retros UI (placeholder)
- [ ] Cloudflare tunnel wiring at projectNexusCode.org
- [ ] Submodule into parent `agile-cards` at `dashboard/`

See `docs/handoffs/HANDOFF_2026-05-17_dashboard-v0plus.md` for the
session-by-session changelog.
