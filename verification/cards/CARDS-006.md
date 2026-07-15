---
AC: AC-CARDS-006
Phase: v1
Status: PASS
Verifier: Claude (K11)
Verified at: 2026-06-30
Evidence: >
  AC text: bearer JWT verification on every authenticated endpoint --
  no token => 401, valid => 200, tampered => 401.

  Implementation:
  - backend/cards_api/deps.py -- `require_claims` is a FastAPI dependency that
    every authed route depends on. It reads the bearer token, runs it through
    the TokenVerifier, and converts any failure into a 401 with a
    `WWW-Authenticate: Bearer` header and a structured `{"error": <code>}` body.
  - Deliberate divergence from the seed runbook: HTTPBearer(auto_error=False)
    plus an explicit missing-token check, because FastAPI's default
    auto_error=True returns 403 (not 401) when the header is absent. The chunk
    contract requires 401 for "no token", so we raise it ourselves.
  - Authed routes: GET /api/cards, GET /api/cards/{id}, GET /api/me,
    POST /api/cards. /healthz stays public (AC-OBS-004).

  Tests (backend/tests/test_endpoint_auth.py, 7 cases, all PASS):
    /healthz public -> 200; no token -> 401 (+ WWW-Authenticate: Bearer);
    garbage bearer -> 401; valid -> 200; tampered -> 401; expired -> 401; and a
    sweep asserting every /api/* route 401s without a token (guards against a
    route shipping without the dependency).

  Command (worktree feat/cards-k11-jwt-auth off origin/main c87bb88):
    pytest -> 32 passed, 1 warning in 0.92s
---

# AC-CARDS-006 -- Bearer JWT verification on every authed endpoint

## The contract

| Case            | Status |
|-----------------|--------|
| No token        | 401    |
| Valid token     | 200    |
| Tampered token  | 401    |
| Expired token   | 401    |
| Public /healthz | 200    |

## How it is enforced

`require_claims` (backend/cards_api/deps.py) is attached to every authenticated
route via `Depends`. A missing or empty bearer credential raises 401 directly
(we use `auto_error=False` precisely so the no-token case is 401, not FastAPI's
default 403). A present-but-invalid token (bad signature, expired, wrong
iss/aud, unknown kid) raises `TokenError`, which is mapped to 401.

A regression sweep (`test_every_authed_route_rejects_missing_token`) hits each
`/api/*` route without a token to catch any endpoint that forgets the guard.

## Audit steps

```bash
cd backend
pytest tests/test_endpoint_auth.py -q
```

## Result

PASS -- no token => 401, valid => 200, tampered/expired => 401, health public.
