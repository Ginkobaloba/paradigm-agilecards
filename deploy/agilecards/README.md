# AgileCards deploy (new stack)

Single-host docker-compose for the rewritten AgileCards stack
(ADR-2026-07-16): FastAPI + Postgres 16 with database-enforced row-level
security, Alembic migrations, and the Vite frontend served by Caddy with a
same-origin proxy to the API.

> **Cutover warning.** This stands up the NEW stack alongside the legacy one
> (`legacy/board-express/docker-compose.yml`), which is still the live board.
> Cutting traffic over -- moving the tunnel/DNS, importing legacy data --
> requires Drew's explicit go-ahead (Tier-3 rule). Bringing this compose up
> does not touch the legacy deployment.

## What comes up

| Service   | Image                        | Role |
|-----------|------------------------------|------|
| postgres  | postgres:16-alpine           | Data. Named volume `pgdata`; first boot runs `initdb/01-app-role.sh` to create the `agilecards_app` LOGIN role (NOSUPERUSER, NOBYPASSRLS). Not published to the host. |
| migrate   | agilecards-api:local         | One-shot `alembic upgrade head` with the owner DSN. The api waits for it to complete successfully. |
| api       | agilecards-api:local         | FastAPI on :8000 (compose-internal). Connects as `agilecards_app` -- the role RLS applies to. |
| web       | agilecards-web:local         | Caddy: static SPA + proxy of `/api/*`, `/events`, `/healthz`, `/readyz` to the api. Host port `${WEB_PORT:-8080}`. |
| dev-idp   | caddy:2-alpine (`--profile dev`) | Static JWKS server for IdP-less local runs. Never in production. |

## TLS: two postures

1. **Cloudflare edge (default).** The web service listens plain HTTP on the
   compose network; a `cloudflared` tunnel connector (see
   `docs/board/cloudflared-tunnel.md`) makes an outbound connection to
   Cloudflare, which terminates public TLS. Nothing here is exposed to the
   internet directly.
2. **Direct hosting.** Uncomment the `cards.paradigm.codes` block in
   `Caddyfile`, publish 443, point DNS at the host; Caddy handles ACME
   certificates, HSTS, and security headers itself.

Details, including the accepted plaintext-inside-compose-network posture and
the `/events?token=` exception: `docs/security/DATA_PROTECTION.md`.

## Run it

From this directory (`deploy/agilecards/`), PowerShell or bash:

```powershell
# 1. Configure. .env is gitignored; use long random passwords.
cp .env.example .env

# 2. Production posture (real IdP issues tokens):
docker compose up -d --build

# 2b. OR dev posture (no IdP). First mint keys + a token -- this also writes
#     dev-keys/jwks.json, which dev-idp serves. Then enable the dev profile
#     and point the backend at it (in .env):
#       PARADIGM_JWKS_URL=http://dev-idp/.well-known/jwks.json
python ..\..\backend\scripts\mint_dev_token.py --org org_dev --sub drew --roles admin,member
docker compose --profile dev up -d --build

# 3. Smoke (WEB_PORT defaults to 8080):
curl http://localhost:8080/healthz          # {"ok":true,...}  liveness
curl http://localhost:8080/readyz           # {"ok":true,...}  DB connectivity
curl http://localhost:8080/api/columns      # 401 {"error":"missing_token"}

# 4. Authed smoke (dev posture):
$token = python ..\..\backend\scripts\mint_dev_token.py --roles admin
curl -H "Authorization: Bearer $token" http://localhost:8080/api/columns   # 200, five columns
curl -H "Authorization: Bearer $token" -H "Content-Type: application/json" `
     -d '{"title":"smoke card"}' http://localhost:8080/api/cards           # 201
```

Migrations run automatically on every `up` (the `migrate` service). To apply
new migrations to a running stack:

```powershell
docker compose up --build migrate
```

## Rollback / teardown

```powershell
docker compose down        # stops containers; the pgdata VOLUME SURVIVES
docker compose down -v     # destroys pgdata too -- data is GONE. Be sure.
```

`down` + `up` with an older image tag is the rollback path: data lives in the
named volume, not the containers. Schema downgrades (`alembic downgrade`) are
a manual, owner-DSN operation and not part of normal rollback.

## Notes

- **Passwords.** `POSTGRES_PASSWORD` (owner; postgres + migrate only) and
  `POSTGRES_APP_PASSWORD` (runtime role; api only) are deliberately separate.
  The API never holds a credential that could bypass RLS.
- **Existing role.** `initdb/01-app-role.sh` runs only on a fresh data volume.
  Against a pre-existing cluster, run its two statements by hand (they are
  idempotent) or execute the script once with the env vars set.
- **Local pytest Postgres** (`agilecards-pg-dev` on host port 5440, see
  `backend/README.md`) is a separate throwaway container -- unrelated to this
  compose and safe to keep running alongside it.
