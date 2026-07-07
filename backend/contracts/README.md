# backend/contracts/ -- consumer contract tests (CARDS-013, chunk K16)

Executable contract tests the AgileCards backend must satisfy as a **consumer**
of the Paradigm auth layer. They are deliberately separate from `backend/tests/`
(the app's own unit tests) and from `backend/pyproject.toml`:

- They run in CI under the dedicated **`contracts`** job (`.github/workflows/ci.yml`),
  on **every PR** and **every Renovate bump of a `@paradigm/*` package**.
- They install their own toolchain from [`requirements.txt`](./requirements.txt),
  so the suite never couples to the backend app's dependency set (owned by K11).

## What's here

- `test_paradigm_auth_contract.py` -- the `@paradigm/auth` token contract
  (**CARDS-013**). `@paradigm/auth` is TypeScript-only; per the integration
  roadmap there is no Python `@paradigm/auth` package in v1, so the FastAPI
  backend verifies IdP-issued JWTs **directly** (RS256 via JWKS, PyJWT). This
  file pins exactly what that verification must enforce:
  - RS256 only -- the HS256 algorithm-confusion forgery and `alg: none` are rejected
  - `kid`-based key resolution from the JWKS
  - JWKS **refresh on an unknown `kid`** (key rotation), fetched exactly once
  - expired tokens rejected
  - tampered signatures rejected

  The suite is self-contained (in-memory RSA keypair + JWKS + a reference
  verifier), so it runs offline and deterministically. The **production**
  verifier is owned by chunk **K11** (`AC-CARDS-003`); when it lands it must
  satisfy this same contract. The copy-paste verifier sample is owned by K-DOC
  (`docs/runbooks/python-jwt-verification.md`).

## Run locally

```powershell
cd C:\dev\paradigm-agilecards\backend
python -m pip install -r contracts\requirements.txt
python -m pytest contracts -q
ruff check contracts
```

The TypeScript counterpart (`@paradigm/llm-client`, **CARDS-014**) lives at
[`../../frontend/contracts/`](../../frontend/contracts/).
