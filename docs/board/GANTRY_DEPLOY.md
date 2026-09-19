# Gantry: build and deploy from main

Gantry is the agile-cards board served at `https://portal.paradigm.codes/gantry/`.
It runs as two containers on DREWSPC, `gantry-board-backend` (host port 4070)
and `gantry-board-frontend` (host port 8110), compose project `board`. The
shared tunnel and demo-proxy route `/gantry/` to port 8110; this stack has no
tunnel of its own.

The recipe lives in [`docker-compose.gantry.yml`](../../docker-compose.gantry.yml)
at the repo root. It builds:

- the frontend from `frontend/` (moved there from `apps/board/frontend` by #44),
- the Express backend from `legacy/board-express/backend/`,

using the Dockerfiles in `legacy/board-express/docker/` with path build args
(`BACKEND_DIR`, `FRONTEND_DIR`, `DOCKER_DIR`). Their defaults keep the old
`legacy/board-express` context layout, so the Dockerfiles are backward
compatible.

The live containers (as of 2026-09-19) were built on 2026-06-26 from commit
`8d43b7a`, `apps/board` (see `docs/board/GANTRY_BUILD_SOURCE.md`, PR #67).
This file reproduces that service shape. The only runtime difference is
`PORTAL_JWKS_URL`, which now points at `https://portal.paradigm.codes/.well-known/jwks.json`
(same keys as the old host). `PORTAL_ISSUER` stays the old-host string on
purpose; see the comments in the compose file.

**Do not deploy to the live Gantry without Drew's go-ahead.**

## Rules

- The live deploy always uses project name **`board`**. That reuses the volume
  `board_board-data`, which holds the board's SQLite DB. A different project
  name creates a new, empty volume.
- Only bring up `backend` and `frontend`, always with `--no-deps`.
- `C:/dev/todo` must exist; it is bind-mounted read/write as `/cards`.
- Make sure `CORS_ORIGIN`, `BASE_PATH`, `PORTAL_JWKS_URL` and `PORTAL_ISSUER`
  are **not** set in your shell unless you mean to override them. The compose
  file reads them with defaults.

## 1. Render and diff (before every deploy)

From the repo root, on a clean checkout of the commit you intend to deploy:

```powershell
docker compose -p board -f docker-compose.gantry.yml config
```

Compare against the live recipe. Extract `8d43b7a:apps/board` to a scratch
directory and render it the same way (its base file defines a `cloudflared`
service that needs `TUNNEL_TOKEN`; a dummy value is fine because only `config`
runs):

```powershell
$old = "$env:TEMP\gantry-8d43b7a"
New-Item -ItemType Directory -Force $old | Out-Null
git archive 8d43b7a apps/board/docker-compose.yml apps/board/docker-compose.gantry.yml -o "$old\old.tar"
tar -xf "$old\old.tar" -C $old
Push-Location "$old\apps\board"
$env:TUNNEL_TOKEN = "unused-render-only"
docker compose -p board -f docker-compose.yml -f docker-compose.gantry.yml config backend frontend > "$old\old.yml"
Remove-Item Env:TUNNEL_TOKEN
Pop-Location
docker compose -p board -f docker-compose.gantry.yml config > "$old\new.yml"
git diff --no-index "$old\old.yml" "$old\new.yml"
```

Expected differences only: build `context` / `dockerfile` paths, the added
path build args, and `PORTAL_JWKS_URL`. Anything else (ports, container names,
volumes, `BASE_PATH`, `PORTAL_ISSUER`, `CORS_ORIGIN`) means stop.

## 2. Optional: throwaway build and smoke test

Never reuse project `board` for this. Use a throwaway project name and an
override that changes container names, host ports and the cards bind. The
named volume then resolves to `<throwaway>_board-data`, not the live one.

```yaml
# verify.override.yml (keep it outside the repo; use ONLY with the throwaway -p)
services:
  backend:
    container_name: gantry-verify-backend
    ports: !override
      - "14070:4070"
    volumes: !override
      - "<scratch copy of C:/dev/todo>:/cards:rw"
      - "board-data:/data"
  frontend:
    container_name: gantry-verify-frontend
    ports: !override
      - "18110:80"
```

```powershell
$p = "gantry-verify-$(Get-Date -Format yyyyMMdd)"
docker compose -p $p -f docker-compose.gantry.yml -f verify.override.yml config   # confirm: no board_board-data, no gantry-board-*, no C:/dev/todo, no 4070/8110 host ports
docker compose -p $p -f docker-compose.gantry.yml -f verify.override.yml up -d --build
curl.exe -s -o NUL -w "%{http_code}\n" http://localhost:18110/gantry/          # 200, HTML references /gantry/assets/
curl.exe -s http://localhost:18110/gantry/healthz                               # {"ok":true,...}
curl.exe -s http://localhost:14070/healthz                                      # {"ok":true,...}
docker compose -p $p -f docker-compose.gantry.yml -f verify.override.yml down -v
docker rmi "$p-backend" "$p-frontend"
```

## 3. Deploy

First keep the running images as the rollback. `up --build` under project
`board` retags `board-backend:latest` / `board-frontend:latest`, and the old
images would otherwise become dangling and prunable.

```powershell
docker tag board-backend:latest  board-backend:rollback-8d43b7a
docker tag board-frontend:latest board-frontend:rollback-8d43b7a
```

(On 2026-09-19 those were `sha256:f60342f83cb9...` and `sha256:8c7a1fd0b987...`.)

Then, from the repo root:

```powershell
docker compose -p board -f docker-compose.gantry.yml up -d --build --no-deps backend frontend
```

Check:

```powershell
docker ps --filter name=gantry-board --format "{{.Names}} {{.Image}} {{.Status}} {{.Ports}}"
curl.exe -s -o NUL -w "%{http_code}\n" http://localhost:8110/gantry/
curl.exe -s http://localhost:8110/gantry/healthz
```

Then open Gantry from the portal tile at `https://portal.paradigm.codes/`
to confirm the portal token handoff (this exercises `PORTAL_JWKS_URL` and
`PORTAL_ISSUER`).

## 4. Rollback

Point the tags back at the saved images and recreate the containers from the
frozen `8d43b7a` recipe without rebuilding:

```powershell
docker tag board-backend:rollback-8d43b7a  board-backend:latest
docker tag board-frontend:rollback-8d43b7a board-frontend:latest

$src = "C:\dev\_deploy\gantry-8d43b7a"
git -C C:\dev\paradigm-agilecards worktree add --detach $src 8d43b7a   # skip if it exists
Push-Location "$src\apps\board"
$env:TUNNEL_TOKEN = "unused-render-only"
docker compose -p board -f docker-compose.yml -f docker-compose.gantry.yml up -d --no-build --no-deps --force-recreate backend frontend
Remove-Item Env:TUNNEL_TOKEN
Pop-Location
```

If the rollback tags were never created, rebuild from the pinned commit with
the recipe in `docs/board/GANTRY_BUILD_SOURCE.md` (same command with `--build`).
The SQLite data in `board_board-data` is untouched by either direction.

## When the portal changes its issuer

Change `PORTAL_ISSUER` in `docker-compose.gantry.yml` in the same deploy in
which the portal changes `env.portalIssuer`, then redeploy with step 3 (a
`--force-recreate` is enough; the federation env is runtime-only). Before the
old portal host is retired, confirm Gantry is on this recipe so it no longer
fetches keys from that host.
