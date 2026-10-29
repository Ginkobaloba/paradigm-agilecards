# Deploying AgileCards (the current stack)

This directory is the deploy artifact for the **FastAPI + Postgres + Vite**
stack — the one the repo actually develops. The compose files under
`legacy/board-express/` build the retired Express app for the old host and
must not be used for new deploys (audit M1/M2).

## Quick start

```powershell
cd deploy
Copy-Item .env.example .env    # then fill in the three passwords
docker compose up --build -d
curl http://localhost:8080/healthz    # -> {"ok": true, "version": ..., "db": "ok"}
```

Services: `postgres` (16, volume `pgdata`), `migrate` (one-shot Alembic as
`cards_owner`), `api` (uvicorn as `cards_app`), `web` (Caddy: static UI +
same-origin proxy for `/api`, `/events`, `/healthz`).

## Security posture (compliance seams)

### Roles (RLS actually binds)

The API connects as `cards_app`: LOGIN only, `NOSUPERUSER`, `NOBYPASSRLS`,
and not the owner of any table. Every tenant table has ENABLE + FORCE row
level security with org policies (see the ADR and
`backend/tests/pg/test_rls_enforcement.py`). Migrations run once as
`cards_owner`. **Never point `CARDS_DATABASE_URL` at a superuser or the
owner role** — the app will still work, which is exactly the problem; the
`FORCE` flag protects against the owner case, superusers bypass RLS by
Postgres design.

Verification (any time):

```sql
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles
WHERE rolname IN ('cards_app', 'cards_owner');
-- cards_app must be f / f
```

### Encryption at rest (AES-256)

Postgres itself does not encrypt storage; the disk does. Pick one:

- **Managed Postgres** (RDS / Cloud SQL / Neon...): enable storage
  encryption at instance creation (AES-256). Preferred for a real alpha.
- **Self-host (this compose):** the Docker volume `pgdata` must live on an
  encrypted disk — BitLocker (Windows hosts), LUKS/dm-crypt (Linux).
  Verify: `manage-bde -status` (Windows) / `lsblk -o NAME,FSTYPE,TYPE`
  shows `crypto_LUKS` (Linux).

App-layer field encryption (pgcrypto) was considered and deliberately
deferred — reasoning in the ADR (D5).

### TLS in transit

Two supported postures:

1. **Cloudflare tunnel** (matches the legacy deploy): the edge terminates
   TLS; `SITE_ADDRESS=:80` and the tunnel points at the published port.
   Nothing plaintext leaves the host.
2. **Direct**: set `SITE_ADDRESS=cards.paradigm.codes` (a real hostname
   with DNS pointing at the host) and Caddy provisions/renews certificates
   automatically. HSTS is already in the Caddyfile.

Compose-internal traffic (web -> api -> postgres) stays on the isolated
compose network.

### Secrets

No secrets in the repo. `deploy/.env` is gitignored; production can switch
`PARADIGM_SECRETS_PROVIDER=infisical` and provide the `INFISICAL_*` machine
identity (the image installs the SDK). The JWT issuer/audience/JWKS values
are public configuration.

### SSE note

`/events` authenticates via `?token=<jwt>` because EventSource cannot set
headers (legacy-compatible). Tokens in URLs can reach proxy logs — the
bundled Caddy config does not log query strings, and the tunnel path
doesn't either; keep that property if you swap the proxy.

## Smoke check (matches verify/smoke.yml expectations)

- `GET /healthz` -> 200, `$.ok == true`
- `GET /api/columns` (no token) -> 401

## Single worker, on purpose

The SSE bus is in-process; run one uvicorn worker per deployment until the
bus moves to Postgres LISTEN/NOTIFY (ADR D7). Horizontal scale before that
change silently fragments live updates.
