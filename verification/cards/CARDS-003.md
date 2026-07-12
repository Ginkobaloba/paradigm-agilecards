---
AC: AC-CARDS-003
Phase: v1
Status: PASS
Verifier: Claude (K11)
Verified at: 2026-06-30
Evidence: >
  AC text (from PARADIGM_INTEGRATION_ROADMAP.md Q2): the FastAPI backend
  verifies JWTs against the IdP JWKS using PyJWT + cryptography (no .NET SDK, no
  Python @paradigm/auth package in v1). Configurable JWKS URL; mockable for tests.

  Implementation:
  - backend/cards_api/auth.py -- TokenVerifier resolves the signing key from a
    JWKS by `kid` and validates an RS256 signature plus iss/aud/exp/nbf/iat.
    `algorithms=["RS256"]` is a hard allowlist (no HS256, no `none`), which is
    what makes the symmetric-confusion attack impossible (AC-AUTH-003/SEC-003).
  - JWKS source is configurable: real path constructs PyJWKClient(jwks_url) with
    in-memory caching (lifespan=600); jwks_url is derived from PARADIGM_JWT_ISSUER
    or set explicitly via PARADIGM_JWKS_URL.
  - Mockable for tests: TokenVerifier takes an injected `jwk_client`, so the
    suite runs fully offline against an in-process RSA keypair + fake JWKS
    (tests/conftest.py). No network in CI.
  - Dependency: pyjwt[crypto]>=2.8 added to backend/pyproject.toml base deps
    (the [crypto] extra pulls in `cryptography` for RS256).

  Tests (backend/tests/test_auth_verify.py, 10 cases, all PASS):
    valid -> claims; tampered signature -> reject; HS256 -> reject; expired ->
    token_expired; nbf-in-future -> reject; wrong iss -> bad_issuer; wrong aud ->
    bad_audience; unknown kid -> reject; missing org_id -> missing_org_id;
    malformed -> reject.

  Command (worktree feat/cards-k11-jwt-auth off origin/main c87bb88):
    ruff check .            -> All checks passed!
    pytest                  -> 32 passed, 1 warning in 0.92s
  (The 1 warning is an upstream starlette/httpx testclient deprecation present
  in the K2 baseline, not from this change.)
---

# AC-CARDS-003 -- Direct JWKS JWT verification (PyJWT + cryptography)

## What was built

`backend/cards_api/auth.py`: a `TokenVerifier` that verifies Paradigm RS256
access tokens against a JWKS. Key design points:

- **RS256-only allowlist.** `jwt.decode(..., algorithms=["RS256"])` rejects
  HS256 and `none` outright. This is the defense against the
  public-key-as-HMAC-secret confusion attack.
- **JWKS by `kid`, cached.** Production uses `PyJWKClient(jwks_url)`, which
  selects the key by `kid` and refetches on an unknown `kid` (rotation).
- **Configurable + mockable.** The JWK client is dependency-injected, so tests
  supply an offline fake (no network). The URL is configurable via settings.
- **Claim validation.** `iss`, `aud`, `exp`, `nbf`, `iat`, `sub` are required and
  verified; `org_id` is required for downstream authorization.

## Audit steps

```bash
cd backend
ruff check .
pytest tests/test_auth_verify.py -q
```

Expected: ruff clean; all verifier cases pass (valid / tampered / HS256 /
expired / nbf / wrong-iss / wrong-aud / unknown-kid / missing-org_id /
malformed).

## Result

PASS -- direct JWKS RS256 verification via PyJWT + cryptography, configurable
URL, offline-mockable, with full reject-path coverage.
