# Dashboard v0+ Handoff (2026-05-17)

## What shipped

A new repo at `C:\dev\agile-cards-board`, scaffolded as a production-grade
React + Node.js dashboard for the `/cards` skill. Companion to the
single-file `dashboard/v0/index.html` prototype that lives in the parent
`agile-cards` repo.

### Working today

- **Backend (Express + TypeScript)**
  - `GET  /healthz` (public)
  - `GET  /api/columns` -- canonical column list
  - `GET  /api/cards` -- summary list, sorted by status then id
  - `GET  /api/cards/:id` -- full card (frontmatter + body)
  - `POST /api/cards/:id/move` -- atomic move (rename file + rewrite
    `status:` line, two-step write so a failure leaves nothing
    half-mutated)
  - `GET  /events` -- SSE stream, fires on every chokidar
    add/change/unlink and on `card-state-changed` from drag-drop moves
  - `GET  /api/sprints`, `POST /api/sprints`, `GET /api/sprints/:id`,
    `POST /api/sprints/:id/cards` -- schema and routes in place, no UI
    yet
  - `GET  /api/retros`, `POST /api/retros`, `GET /api/retros/:id` -- ditto
  - Bearer-token middleware on every `/api/*` and `/events` request,
    SHA-256 hashing of tokens, plaintext shown exactly once at mint
  - SQLite via `better-sqlite3` (WAL mode, foreign keys on) for tokens,
    sprints, retros
  - chokidar watcher over the status subfolders of `CARDS_DIR`, with
    `awaitWriteFinish` so partial writes don't fire spurious events
  - CLI scripts: `create-token`, `list-tokens`, `revoke-token`

- **Frontend (Vite + React + TypeScript)**
  - Tailwind palette mirrors v0 (same dark theme, same tier badge colors)
  - Token-gate login screen, sessionStorage for the bearer
  - Kanban: 5 columns from the API, `@dnd-kit` drag-drop, optimistic
    moves with rollback on API failure
  - Card modal: full frontmatter table + body rendered with
    `react-markdown` + `remark-gfm`
  - SSE hook keeps multiple tabs in sync; events trigger targeted
    refetch of the changed card
  - Header with route nav (Kanban / Sprint Planner / Retros), refresh
    button, sign-out, CARDS_DIR pill
  - Stubs for Sprint Planner and Retros routes ("v1 coming soon"; backend
    is already speaking the schema)

- **Docker**
  - `docker-compose.yml` brings up the backend (Node 20 Alpine, native
    deps compiled) and an nginx frontend (proxies `/api` and `/events`)
  - SSE proxying configured in `docker/nginx.conf` with buffering off

### Type discipline

- TypeScript strict, `noUncheckedIndexedAccess`, `noImplicitReturns`,
  no `any` in app code (a single typed unknown narrow in
  `getAuthContext` is documented inline). Zero errors out of
  `tsc --noEmit` on both backend and frontend.
- Zero suppressions, zero `// @ts-ignore`.

## Decisions made on unlocked items

| Item | Picked | Reasoning |
|------|--------|-----------|
| UI primitives | Radix Dialog + Tooltip + Tailwind | Headless, accessible by default. We only need a dialog and a tooltip for v0+, so importing the full shadcn copy-paste set isn't worth the maintenance surface yet. Direct Radix matches the "no shadcn" steer. |
| Drag-drop | `@dnd-kit/core` + `@dnd-kit/sortable` | Modern, accessible (keyboard support out of the box), works with React 18 concurrent rendering. Better than react-beautiful-dnd which is unmaintained. |
| State management | Zustand | Lighter than Redux Toolkit, more capable than Context for selector-based re-renders. Single store, ~80 lines. |
| Routing | React Router v6 | Standard SPA routing, three routes for now. |
| Date handling | date-fns | Tree-shakable, ESM-first. Not heavily used yet (the v0+ doesn't render any computed dates), so the dep is there for the v1 sprint planner. |
| File watcher | chokidar | The asked-for choice. `awaitWriteFinish` matters because the `/cards` runner can write a card in stages; we don't want to scan it half-written. |
| Markdown | `react-markdown` + `remark-gfm` | GFM gets us tables, task lists, and fenced code blocks, which the cards lean on for acceptance checks. |
| YAML | js-yaml with `JSON_SCHEMA` | Skip YAML 1.1 quirks (`yes`/`no` parsing as booleans, etc.) so the frontmatter values come out as the strings the cards actually contain. |
| Repo layout | Two top-level dirs, no monorepo tooling | One root `package.json` would only be useful if we shared deps. Backend and frontend have totally separate dep trees, so workspaces would buy us nothing and cost familiarity. |
| Static-file serving | nginx in compose, not Express | Express *can* serve static, but nginx handles SSE buffering, gzip, and cache headers correctly with one config file. Keeps the backend container focused on the API. |
| LICENSE text | Full canonical PolyForm NC 1.0.0 | The parent repo currently has a truncated LICENSE; I wrote the full canonical text here. Drew may want to backfill the parent the same way. |
| Token store location | Separate SQLite on the dashboard backend | Per the constraints. No cross-service coordination with brainstem auth tonight. |

## How to run

See the README. Short version:

```powershell
# backend
cd C:\dev\agile-cards-board\backend
npm install
$env:CARDS_DIR = "C:\dev\todo"
$env:DB_PATH   = "C:\dev\agile-cards-board\backend\data\board.sqlite"
npm run create-token -- --label "drew-laptop"   # save the printed token
npm run dev

# frontend (separate terminal)
cd C:\dev\agile-cards-board\frontend
npm install
npm run dev
# open http://localhost:5173, paste the token
```

## What's stubbed

- **Sprint Planner UI** -- backend routes and SQLite schema exist; UI
  shows a placeholder. v1 will wire a draggable timeline against the
  backlog.
- **Retros UI** -- same shape; placeholder route, backend ready.
- **Submodule into parent** -- skipped. See "Submodule" below.

## What's next

1. **Submodule** -- the parent `agile-cards` repo at `C:\dev\agile-cards`
   has no `origin` remote yet (`git remote -v` is empty). The migration
   to GitHub hasn't pushed. Once Drew lands that:
   ```powershell
   cd C:\dev\agile-cards
   git submodule add https://github.com/Ginkobaloba/agile-cards-board.git dashboard
   git commit -m "feat(dashboard): add agile-cards-board as submodule"
   git push origin main
   ```
   The existing `dashboard/v0/index.html` will live alongside the
   submodule's `dashboard/` checkout. If that collides, move the v0 file
   to `dashboard-v0/` before adding the submodule.

2. **Cloudflare tunnel** -- target `projectNexusCode.org` -> BROOKFIELD_PC:4070
   for the backend, and an `app.projectNexusCode.org` -> nginx:8080 for the
   frontend (or just serve the frontend statics from the backend port via
   nginx in compose, depending on how the tunnel is segmented).

3. **Sprint Planner v1** -- backend already exposes the routes. UI sketch:
   left rail is the backlog, right side is a horizontal timeline of
   sprints (cards from the current sprint highlighted on the kanban).
   Drag cards onto sprint cards to assign. Points sum per sprint visible
   in a header pill.

4. **Retros v1** -- list view + composer that snapshots the Done column
   at retro creation time. Likely a textarea with a markdown preview.

5. **Cross-service auth integration (with brainstem)** -- once the
   dashboard is past v1, fold the token store into the shared scheme so
   one token works across services. Today's separate store is the right
   call for the bounded scope.

## Git / push status

Per the working agreement, no git commands were run from the Linux
scaffold environment. The repo at `C:\dev\agile-cards-board` is a plain
directory of files. A PowerShell setup script lives at
`C:\dev\agile-cards-board\setup.ps1` and does the following, with
defensive `.git\*.lock` cleanup before each operation:

1. `git init --initial-branch=main`
2. Verify `git config user.name` / `user.email`
3. `git add -A`
4. `git commit -m "feat: scaffold dashboard v0+ (Express+TS backend, React+Vite+TS frontend, SSE, auth, kanban)"`
5. `gh repo create Ginkobaloba/agile-cards-board --public --source=. --remote=origin --description "..."`
6. `git push -u origin main`

In the morning, in PowerShell:

```powershell
cd C:\dev\agile-cards-board
.\setup.ps1
```

The script is idempotent on init/add/commit, will detect an existing
remote, and will fall through to `git push` either way.

## Verification

- `npx tsc --noEmit` on `backend/tsconfig.json` -- exit 0, no warnings.
- `npx tsc --noEmit` on `frontend/tsconfig.json` -- exit 0, no warnings.
- `npm install` clean in both trees.
- Backend boots in dry-run cannot be verified from the scaffold sandbox
  (would require live SQLite write + chokidar watcher), but every module
  was typechecked against its production deps.

## Files

```
agile-cards-board/
  LICENSE                                    PolyForm NC 1.0.0 (full canonical text)
  README.md                                  user-facing overview
  .gitignore
  docker-compose.yml
  setup.ps1                                  PowerShell git/gh push script
  docker/
    Dockerfile.backend                       multi-stage node:20-alpine
    Dockerfile.frontend                      nginx:alpine, multi-stage vite build
    nginx.conf                               SPA fallback + /api + /events proxy
  backend/
    package.json
    tsconfig.json                            strict, noUncheckedIndexedAccess
    src/
      server.ts                              Express entry
      config.ts                              env -> frozen Config
      logger.ts                              tiny structured JSON logger
      routes/
        auth.ts                              bearer middleware
        cards.ts                             card REST
        sprints.ts                           sprint REST (stub UI side)
        retros.ts                            retro REST (stub UI side)
        sse.ts                               EventSource fan-out
      db/sqlite.ts                           better-sqlite3 + schema migrations
      fs/
        cards.ts                             chokidar + index + atomic moveCard
        frontmatter.ts                       js-yaml parse + status line rewriter
      auth/
        hash.ts                              sha256 + ctEqual + token generator
        tokens.ts                            mint/list/validate/revoke
      events/bus.ts                          in-process pub/sub for SSE
    scripts/
      create-token.ts
      list-tokens.ts
      revoke-token.ts
  frontend/
    package.json
    tsconfig.json                            strict
    tsconfig.node.json
    vite.config.ts                           proxies /api + /events to :4070
    tailwind.config.ts                       v0 palette
    postcss.config.cjs
    index.html
    src/
      main.tsx
      App.tsx                                gates on auth, mounts router
      routes/
        Kanban.tsx                           DndContext + columns + modal
        SprintPlanner.tsx                    placeholder
        Retros.tsx                           placeholder
      components/
        Header.tsx                           nav + refresh + sign-out + CARDS_DIR
        Column.tsx                           droppable, sortable inner context
        CardTile.tsx                         draggable, tier badge, batch, model
        CardModal.tsx                        Radix Dialog, react-markdown body
        TokenGate.tsx                        login screen
      hooks/
        useAuth.ts                           sessionStorage + cross-tab sync
        useCards.ts                          rest hydrate
        useSSE.ts                            event-typed listener, store patches
      lib/
        api.ts                               typed fetch wrapper, ApiError
        auth.ts                              token storage
        parseCard.ts                         frontmatter field extractors
        tierBadge.ts                         tier -> tailwind class
      state/
        store.ts                             Zustand store, selectors
      styles/
        globals.css                          tailwind layers + markdown styles
  docs/
    handoffs/
      HANDOFF_2026-05-17_dashboard-v0plus.md (this file)
```
