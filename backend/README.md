# paradigm-agilecards backend (FastAPI)

Python/FastAPI backend for Paradigm AgileCards. Locked to Python per the
integration roadmap (Q2). Chunk **K11** landed the auth core
(AC-CARDS-003/006/007/008); ADR-2026-07-16 lands the rest for real: Postgres
persistence with **database-enforced row-level security**, full board CRUD at
legacy wire parity, an append-only audit trail, and the SSE live channel.

## Layout

```
backend/
  app.py                 entrypoint shim (uvicorn app:app) re-exporting the package
  cards_api/
    auth.py              TokenVerifier -- RS256 JWKS verification (AC-CARDS-003)
    deps.py              require_claims / require_roles guards + RLS-bound sessions
    db.py                engine/sessions; binds the verified org_id to the RLS GUC
    config.py            boot-time settings + Infisical secret loading (AC-CARDS-008)
    main.py              FastAPI app factory
    routers/             cards, columns, ranks, sprints, retros, views, stories,
                         triage, rates, SSE, audit admin, health probes
  migrations/            Alembic; 0001 creates schema + roles + RLS policies
  scripts/
    mint_dev_token.py    DEV-ONLY RS256 token mint for the compose dev profile
  tests/                 auth suite (offline) + RLS/org-isolation suite (real Postgres)
  Dockerfile             production image (api + one-shot migrate roles)
  .env.example           placeholder config (no real secrets)
  pyproject.toml         deps + dev/infisical extras
```

## Auth model

- **AC-CARDS-003** -- `auth.py` verifies RS256 access tokens against the IdP
  JWKS (`PyJWKClient`, key-by-`kid`, cached). `algorithms=["RS256"]` is a hard
  allowlist: HS256 and `none` are rejected, so the symmetric-confusion attack is
  impossible. The JWK client is dependency-injected, so tests run offline.
- **AC-CARDS-006** -- `require_claims` guards every authed route. No token =>
  401, valid => 200, tampered/expired => 401. `/healthz` stays public.
- **AC-CARDS-007** -- `org_id` and `roles` come from the verified token only.
  Reads/writes are org-scoped at the store boundary (cross-org reads => 404);
  `require_roles("admin")` gates mutation.
- **AC-CARDS-008** -- `config.py` sources secrets from Infisical at boot when
  `PARADIGM_SECRETS_PROVIDER=infisical`, else falls back to env vars (CI/dev).
  No real secrets in the repo; `.env.example` is placeholders only.

There is no Python `@paradigm/auth` SDK in v1; this is direct JWKS verification.
The copy-paste seed is `paradigm-platform/docs/runbooks/python-jwt-verification.md`.

## Endpoints

Full legacy-parity surface: `/api/cards` CRUD + frontmatter/move/rank/events,
`/api/columns`, `/api/ranks`, `/api/views`, `/api/sprints`, `/api/retros`,
`/api/stories` + triage, `/api/rates`, `/events` (SSE), `/api/audit` (admin),
plus unauthenticated `/healthz` (liveness) and `/readyz` (DB connectivity).
Everything under `/api` and `/events` requires a Paradigm bearer token; wire
shapes match the legacy Express contract (see `cards_api/routers/`).

## Run locally

```powershell
cd C:\dev\paradigm-agilecards\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]          # add .[infisical] in production images

# 1. Disposable dev Postgres (host port 5440; also what pytest expects).
docker run -d --name agilecards-pg-dev -e POSTGRES_PASSWORD=devpassword `
    -e POSTGRES_DB=agilecards -p 5440:5432 postgres:16-alpine

# 2. Lint + tests. The suite creates a uniquely-named database per session,
#    migrates it, and connects as the agilecards_app role, so RLS is proven
#    for real. It FAILS (never skips) if Postgres is unreachable.
#    Non-default Postgres? Set AGILECARDS_TEST_ADMIN_DSN to an admin DSN.
ruff check .
pytest -q

# 3. Migrate the dev database (owner DSN -- migrations manage roles/RLS/DDL)
#    and give the runtime role a login password (dev-only value):
$env:PARADIGM_DATABASE_MIGRATE_URL = "postgresql+psycopg://postgres:devpassword@localhost:5440/agilecards"
alembic upgrade head
docker exec agilecards-pg-dev psql -U postgres -d agilecards `
    -c "ALTER ROLE agilecards_app LOGIN PASSWORD 'devapppassword'"

# 4. Serve. The app connects as agilecards_app -- NEVER point
#    PARADIGM_DATABASE_URL at an owner/superuser DSN (it bypasses RLS).
$env:PARADIGM_DATABASE_URL = "postgresql+psycopg://agilecards_app:devapppassword@localhost:5440/agilecards"
uvicorn app:app --reload       # http://127.0.0.1:8000/healthz , /readyz
```

Tokens for local calls: `python scripts/mint_dev_token.py --roles admin`
(DEV-ONLY -- the server accepts these only when `PARADIGM_JWKS_URL` points at
the matching dev JWKS; see the script docstring).

## Deploy

The production-shaped compose stack (Postgres + one-shot migrate + api + Caddy
web, with a dev-profile JWKS server) lives in `../deploy/agilecards/` -- see
its README for the runbook, TLS postures, and rollback notes.
