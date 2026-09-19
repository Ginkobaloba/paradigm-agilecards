# Gantry: where the live containers were built from, and how to rebuild them

**Verified 2026-09-19.** Gantry is the board served at
`https://portal.paradigm.codes/gantry/`. It runs as two local Docker containers
on DREWSPC, `gantry-board-backend` and `gantry-board-frontend` (compose project
`board`). They were built on 2026-06-26 and have not been rebuilt since.

## The problem this answers

The containers record their compose working directory as
`C:\dev\agile-cards\apps\board`. That directory no longer exists. The repo was
renamed and restructured in #44 (`apps/board` became `legacy/board-express`),
and the restructure **deleted `frontend/`** (and `marketing/`) from that path.
So neither the recorded path nor current `main` can rebuild the live frontend.

## The source that matches, proven

The live containers were built from **this repo at commit `8d43b7a`**
("feat(board): embed Gantry portal tile with JWKS federation + base-path
deploy fixes (#42)", 2026-06-26 13:41 CDT), directory `apps/board`, using both
`docker-compose.yml` and `docker-compose.gantry.yml`.

Proof: Docker Compose stores a hash of each service's resolved config in the
`com.docker.compose.config-hash` label. Rendering that commit's compose files
with project name `board` gives identical hashes for both services:

| Service | Live container label | Rendered from `8d43b7a:apps/board` |
|---|---|---|
| backend | `cac4af505fb64b49e37b1d86161e568d0880a075d92d8a71a30d2a84b01fe27c` | same |
| frontend | `18a455ca2c2862f276c6907f2e83b7434959329324c500c579116c3f9dd4630d` | same |

The images are local builds with no registry: `board-backend:latest`
(sha256:f60342f8..., created 2026-06-26 04:59 UTC) and `board-frontend:latest`
(sha256:8c7a1fd0..., created 2026-06-26 05:05 UTC).

## Rebuild recipe

```powershell
# 1. Materialize the exact source tree (outside any live checkout).
$src = "C:\dev\_deploy\gantry-8d43b7a"
git -C C:\dev\paradigm-agilecards worktree add --detach $src 8d43b7a

# 2. Render first. The base file also defines a `cloudflared` service that needs
#    TUNNEL_TOKEN; Gantry does NOT run it (the shared tunnel routes /gantry/),
#    so a dummy value is enough to render, and we only bring up two services.
cd "$src\apps\board"
$env:TUNNEL_TOKEN = "unused-render-only"
docker compose -p board -f docker-compose.yml -f docker-compose.gantry.yml config --hash "backend,frontend"
#    Expect the two hashes in the table above. If they differ, stop.

# 3. Rebuild and replace ONLY the two Gantry services.
docker compose -p board -f docker-compose.yml -f docker-compose.gantry.yml up -d --build --no-deps backend frontend
Remove-Item Env:TUNNEL_TOKEN
```

Rules:
- Always use project name **`board`**. A different name mints a new, empty
  `<project>_board-data` volume instead of reusing `board_board-data`, which
  holds the board's SQLite DB (see memory `compose-volume-name-collides-with-prod`).
- Never start the `cloudflared` service from this file.
- The backend bind-mounts `C:/dev/todo` read/write as `/cards`. That folder
  must exist.

## What the rendered config pins (worth knowing)

- Backend on host port 4070, frontend on host port 8110, `BASE_PATH=/gantry/`.
- `PORTAL_ISSUER` and `PORTAL_JWKS_URL` point at the **old** host
  `portal.projectnexuscode.org`. The portal's JWT issuer stayed on the old
  host (see portal-shell history), so this is expected. But a rebuild bakes it
  in again, and if the old host ever stops serving the JWKS, Gantry
  federation will break.
- `CORS_ORIGIN` allows both the old and the new portal hosts.

## Open follow-up

This makes Gantry **rebuildable**, not **maintainable**. Dependency bumps on
`main` (#45, #63) touched `legacy/board-express` but can't reach a frontend that
no longer exists there. Two options: restore `frontend/` under
`legacy/board-express` from `8d43b7a`, or move Gantry onto the new
paradigm-agilecards frontend. That call is for Drew and is not made here.
