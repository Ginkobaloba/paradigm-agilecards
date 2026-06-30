# paradigm-agilecards backend (FastAPI)

Python/FastAPI backend for Paradigm AgileCards. Locked to Python per the
integration roadmap (Q2). Chunk **K11** lands the auth core
(AC-CARDS-003/006/007/008): direct JWKS JWT verification, a bearer guard on
every authed endpoint, org-scoped isolation + role authorization, and
Infisical-sourced secrets at boot.

## Layout

```
backend/
  app.py                 entrypoint shim (uvicorn app:app) re-exporting the package
  cards_api/
    auth.py              TokenVerifier -- RS256 JWKS verification (AC-CARDS-003)
    deps.py              require_claims / require_roles guards (AC-CARDS-006/007)
    store.py             org-scoped in-memory card store (AC-CARDS-007)
    config.py            boot-time settings + Infisical secret loading (AC-CARDS-008)
    main.py              FastAPI app factory + routes
  tests/                 offline auth/isolation/config suite (no network)
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

| Method | Path               | Auth                | Notes                          |
|--------|--------------------|---------------------|--------------------------------|
| GET    | `/healthz`         | public              | liveness probe                 |
| GET    | `/api/me`          | bearer              | echoes `{sub, org_id, roles}`  |
| GET    | `/api/cards`       | bearer              | caller-org cards only          |
| GET    | `/api/cards/{id}`  | bearer              | 404 for other orgs' cards      |
| POST   | `/api/cards`       | bearer + `admin`    | org_id taken from the token    |

The card surface is intentionally narrow -- enough to prove the auth and
isolation contract. The full card CRUD rewrite of the legacy Express backend
(`../legacy/board-express/backend`) is a later chunk.

## Run

```powershell
cd C:\dev\paradigm-agilecards\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]          # add .[infisical] in production images

ruff check .
pytest -q

# dev server (env-var config; defaults point at the Paradigm IdP)
uvicorn app:app --reload       # http://127.0.0.1:8000/healthz
```
