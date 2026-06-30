---
AC: AC-CARDS-008
Phase: v1
Status: PASS
Verifier: Claude (K11)
Verified at: 2026-06-30
Evidence: >
  AC text: secrets sourced from Infisical at boot (infisical-python). No real
  .env secrets in the repo. Gitleaks clean.

  Implementation:
  - backend/cards_api/config.py -- load_settings() resolves config at boot. The
    secret SOURCE is selected by PARADIGM_SECRETS_PROVIDER:
      * "infisical" -> load_from_infisical() fetches secrets at boot via the
        infisical-python SDK (import `infisical_client`) using a Universal Auth
        machine identity (client id/secret injected by the runtime, never in the
        repo).
      * anything else ("env", default) -> reads os.environ. This keeps CI and
        local dev free of an Infisical dependency.
  - The infisical SDK is an OPTIONAL import (the `infisical` extra in
    pyproject.toml), required only when actually configured for Infisical, so
    base CI installs/runs without it.
  - JWT issuer/audience/JWKS are public, non-secret config with safe defaults
    pointing at the Paradigm IdP, so the app imports with zero configuration.

  No real .env secrets:
  - backend/.env.example ships placeholders only (PARADIGM_*, INFISICAL_* are
    documented but blank/commented). .gitignore ignores .env and .env.* with an
    explicit `!.env.example` negation so only the template is tracked.
  - test_config_secrets.py asserts no committed backend/.env exists, plus the
    env/infisical source-selection and default behavior (6 cases, all PASS).

  Gitleaks clean (worktree feat/cards-k11-jwt-auth):
    gitleaks detect --no-git --source backend/cards_api   -> exit 0 (no leaks)
    gitleaks detect --no-git --source backend/tests       -> exit 0 (no leaks)
    gitleaks detect --no-git --source backend/.env.example-> exit 0 (no leaks)
    gitleaks detect (full git history, origin/main, 85 commits) -> "no leaks found"
  (A --no-git scan of backend/.venv flags vendored library files, e.g.
  cryptography/*.pyi and jwt/algorithms.py; .venv is gitignored and never
  committed, so it is out of scope for the repo-cleanliness audit.)

  Note: the live Infisical fetch path (load_from_infisical) is exercised by the
  deploy chunk's smoke test against a real Infisical project, not by unit CI
  (it is import-guarded and behind PARADIGM_SECRETS_PROVIDER=infisical).
---

# AC-CARDS-008 -- Secrets from Infisical at boot; no .env secrets; gitleaks clean

## Boot-time secret loading

`backend/cards_api/config.py::load_settings()` picks its secret source at boot
from `PARADIGM_SECRETS_PROVIDER`:

- `infisical` -> `load_from_infisical()` authenticates with a Universal Auth
  machine identity and lists the project's secrets via the `infisical-python`
  SDK. No secret material lives in the repo; the identity credentials are
  injected by the runtime environment.
- default (`env`) -> reads `os.environ`, so CI and local dev need no Infisical
  dependency. The SDK is an optional `[infisical]` extra.

## No real .env secrets

- `backend/.env.example` is placeholders only.
- `.gitignore`: `.env`, `.env.*`, `*.local.env` are ignored; `!.env.example`
  keeps the template tracked.
- `test_config_secrets.py::test_no_committed_dotenv_secret_file` fails if a real
  `backend/.env` is ever committed.

## Gitleaks

```bash
# committed history
gitleaks detect                       # -> "no leaks found" (85 commits)
# working-tree deliverables
gitleaks detect --no-git --source backend/cards_api    # exit 0
gitleaks detect --no-git --source backend/tests        # exit 0
gitleaks detect --no-git --source backend/.env.example # exit 0
```

`.venv` is excluded (gitignored, never committed); a `--no-git` scan of it only
surfaces vendored third-party library files.

## Audit steps

```bash
cd backend
pytest tests/test_config_secrets.py -q
gitleaks detect --no-git --source cards_api
```

## Result

PASS -- Infisical-at-boot wiring present (env fallback for CI/dev), no real
secrets in the tree, gitleaks clean.
