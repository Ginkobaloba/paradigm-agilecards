---
AC: AC-CARDS-013
Phase: v1
Status: PASS
Verifier: Claude (K16)
Verified at: 2026-06-30
Evidence: >
  AC text (from the K-DOC README seed / roadmap K16): "Consumer contract tests
  in paradigm-agilecards against @paradigm/auth -- token verify, JWKS refresh on
  unknown kid, expired/tampered rejection (RS256 + kid + JWKS resolution). Run on
  every PR and every Renovate bump of a @paradigm/* package."

  Architecture note: @paradigm/auth is TypeScript-only; per the integration
  roadmap there is NO Python @paradigm/auth package in v1 (the FastAPI backend
  verifies IdP JWTs directly with PyJWT/cryptography). So CARDS-013 is an
  executable contract: it pins what the IdP / @paradigm/auth issues and what any
  Paradigm Python consumer MUST enforce. The production verifier is K11
  (AC-CARDS-003); it must satisfy this same contract when it lands.

  Deliverable:
  - backend/contracts/test_paradigm_auth_contract.py -- self-contained
    (in-memory RSA keypair + JWKS + a reference verifier), offline, deterministic.
  - backend/contracts/requirements.txt -- pyjwt + cryptography + pytest + ruff
    (kept out of backend/pyproject.toml so the suite does not couple to the K11
    app dependency set).
  - .github/workflows/ci.yml -- new `contracts` job runs it on every PR (and thus
    every Renovate @paradigm/* bump, since those are PRs).

  Coverage (9 tests, all PASS):
  - valid RS256 token resolved by kid -> claims (org_id, roles, sub) returned
  - unknown kid triggers JWKS refresh then verifies (rotation); fetch count +1
  - unknown kid absent after refresh -> rejected; exactly ONE refresh (no loop)
  - expired token -> rejected ("token_expired")
  - tampered signature (real signature byte flipped) -> rejected ("bad_signature")
  - HS256 algorithm-confusion forgery (public key as HMAC secret) -> rejected
    ("bad_algorithm")
  - alg: none -> rejected ("bad_algorithm")
  - missing kid -> rejected ("missing_kid")
  - malformed token -> rejected ("malformed_token")

  Local run (2026-06-30):
    cd backend
    python -m pytest contracts -q   -> 9 passed in 0.30s
    python -m ruff check contracts  -> All checks passed!
---

# AC-CARDS-013 -- Python consumer contract test against `@paradigm/auth`

## Audit steps

```powershell
cd C:\dev\paradigm-agilecards\backend
python -m pip install -r contracts\requirements.txt
python -m pytest contracts -q
ruff check contracts
```

Expected: all contract tests pass, ruff clean. CI runs the same in the
`contracts` job (`.github/workflows/ci.yml`) on every PR and every Renovate
`@paradigm/*` bump.

## Result

PASS -- 9/9 contract tests pass and lint clean. The contract pins RS256 + kid +
JWKS resolution, JWKS refresh on an unknown kid (fetched exactly once), and
expired / tampered / algorithm-confusion rejection. Wired into CI as a
non-required `contracts` context (promote in branch protection after it bakes).
The K11 production verifier (AC-CARDS-003) must satisfy this same contract.
