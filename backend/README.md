# paradigm-agilecards backend (FastAPI)

Python/FastAPI backend for Paradigm AgileCards. Locked to Python per the
integration roadmap (Q2). This directory is a **K2 scaffold only**: it exists
so the repo has the target `backend/` shape and a health endpoint that makes
CI meaningful.

## What lives here now (K2)

- `app.py` -- a FastAPI app exposing `GET /healthz`.
- `pyproject.toml` -- dependencies (FastAPI + uvicorn) and dev extras (pytest, httpx, ruff).
- `tests/` -- a smoke test asserting `/healthz` returns `200 {"status": "ok"}`.

## What is intentionally NOT here yet

The real Cards API is owned by **chunk K11** (AC-CARDS-003/006/007/008):

- Direct JWKS JWT verification (PyJWT + cryptography, or python-jose).
- `401` on missing / expired / tampered tokens.
- `org_id` / `roles` extraction from verified claims, applied to authorization
  and org isolation.
- Secrets pulled from Infisical at boot (no `.env` secrets).

The pre-Paradigm Express/TypeScript backend that K11 rewrites from lives at
[`../legacy/board-express/backend`](../legacy/board-express/backend). Delete
that tree once K11 lands.

## Run

```powershell
cd C:\dev\paradigm-agilecards\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]

ruff check .
pytest -q

# dev server
uvicorn app:app --reload   # http://127.0.0.1:8000/healthz
```
