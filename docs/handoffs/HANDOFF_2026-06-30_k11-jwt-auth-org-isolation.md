# HANDOFF 2026-06-30 -- K11: FastAPI auth core (JWKS, org isolation, Infisical)

Executed **CHUNK K11** (T3; owns AC-CARDS-003/006/007/008) per
`C:\dev\PARADIGM_INTEGRATION_ROADMAP.md`. Replaced the K2 backend scaffold with
the real Cards API auth layer. No LLM calls (that is K11b).

## What this session did

- **AC-CARDS-003** -- `backend/cards_api/auth.py`: `TokenVerifier` verifies RS256
  access tokens against the IdP JWKS (PyJWT + cryptography, `PyJWKClient`,
  key-by-`kid`, cached). `algorithms=["RS256"]` hard allowlist rejects HS256 and
  `none`. The JWK client is dependency-injected, so the suite runs fully offline
  (in-process RSA keypair + mock JWKS in `tests/conftest.py`).
- **AC-CARDS-006** -- `backend/cards_api/deps.py`: `require_claims` guards every
  authed route. No token => 401, valid => 200, tampered/expired => 401; health
  stays public. Uses `HTTPBearer(auto_error=False)` so missing-token is 401, not
  FastAPI's default 403 (deliberate divergence from the seed runbook).
- **AC-CARDS-007** -- `org_id` + `roles` from the verified token only.
  `store.py::CardStore` is org-scoped at the boundary (cross-org read => 404);
  `require_roles("admin")` gates mutation; `POST /api/cards` takes `org_id` from
  the token, never the body. Two-org isolation proven both directions.
- **AC-CARDS-008** -- `backend/cards_api/config.py`: secrets from Infisical at
  boot (`infisical-python`, optional `[infisical]` extra) selected via
  `PARADIGM_SECRETS_PROVIDER=infisical`, with env-var fallback for CI/dev.
  `.env.example` is placeholders only; `.gitignore` keeps the template tracked
  via `!.env.example`.
- **Tests:** 32 passing, offline. `ruff check .` clean. Gitleaks clean (full
  history "no leaks found"; working-tree scan of `cards_api`/`tests`/`.env.example`
  all exit 0).
- **Verification:** `verification/cards/CARDS-003.md` / `006` / `007` / `008` =
  **PASS**.
- **PR #47** opened to `main`, **no auto-merge** (T3). All CI batteries green +
  `backend (fastapi scaffold)` green + Socket Security clean.

## Process note -- shared checkout / parallel agents (important)

At session start the main checkout `C:\dev\paradigm-agilecards` was on
`feat/verify-suite`; **mid-session the branch flipped to `feat/k16-contract-tests`
and an untracked `backend/tests/test_paradigm_auth_contract.py` (K16 / CARDS-013)
appeared.** A parallel K16 agent is operating in the same working directory.

To avoid racing the shared index (the `.git/index.lock` failure mode in
SESSION_PROTOCOL section 7) and mixing two chunks, K11 was built in an isolated
git worktree off `origin/main`:

- Worktree: `C:\dev\_worktrees\paradigm-agilecards\feat-cards-k11-jwt-auth`
- Branch: `feat/cards-k11-jwt-auth` (commit `b47a31d`), base `origin/main` c87bb88.

K16's file was **not** touched and **not** included in the K11 commit. `vend` was
**not** run on the main checkout (it would push K16's in-progress branch).

## What is currently broken or incomplete

- **Live Infisical fetch path is unit-untested.** `load_from_infisical()` is
  import-guarded and behind `PARADIGM_SECRETS_PROVIDER=infisical`; it must be
  smoke-tested against a real Infisical project in the deploy chunk (K10). The
  `infisical-python` API (import `infisical_client`) was written to the
  documented SDK shape but not run here.
- **Card surface is intentionally narrow** (list/get/create + `/api/me`). The
  full CRUD rewrite of `legacy/board-express/backend` (move, frontmatter patch,
  SSE, ranks) is a later chunk; delete the legacy tree when that lands.
- **One pytest warning** -- upstream starlette/httpx testclient deprecation,
  inherited from the K2 baseline, not from this change.
- **Worktree still on disk.** Remove after PR #47 merges:
  `git worktree remove C:\dev\_worktrees\paradigm-agilecards\feat-cards-k11-jwt-auth`.

## What the next session should do first

1. **Get PR #47 reviewed + merged** (T3, human-gated, bottom-up if stacked).
   After merge, `git worktree remove` the K11 worktree and delete the branch.
2. **Reconcile with K16** (`feat/k16-contract-tests`, CARDS-013). K16's
   `test_paradigm_auth_contract.py` is a self-contained reference verifier; once
   both land on main, consider pointing its contract at the real
   `cards_api.auth.TokenVerifier` so the contract tests the shipped code, not a
   duplicate.
3. **K10 deploy** -- wire `PARADIGM_SECRETS_PROVIDER=infisical` + machine-identity
   env, install `backend` with the `[infisical]` extra, and smoke-test
   `load_from_infisical()` against the real project. Mount the backend under
   `cards.paradigm.codes/api/` (AC-CARDS-005).
4. **K11b** -- LLM calls (out of K11 scope).

## Open questions for Drew

- **Parallel agents in one checkout:** K11 and K16 shared
  `C:\dev\paradigm-agilecards` and the branch flipped mid-session. Future
  parallel chunks should each get their own worktree up front. Want that baked
  into the chunk dispatch?
- **Role model:** K11 uses a simple `require_roles("admin")` for mutation. Is the
  real role taxonomy (owner/admin/member/viewer?) defined anywhere yet, or is
  that a later AC?

## Pointers

- PR: https://github.com/Ginkobaloba/paradigm-agilecards/pull/47
- Seed: `paradigm-platform/docs/runbooks/python-jwt-verification.md`
- Verification: `verification/cards/CARDS-003.md`, `006`, `007`, `008`
- Code: `backend/cards_api/{auth,deps,store,config,main}.py`, `backend/tests/`
- Prior handoff: `docs/handoffs/HANDOFF_2026-06-30_k2-paradigm-agilecards-rename.md`

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md` in this
project (none at repo root yet -- consider adding one), then this file, then run
`vstart`. First action is getting PR #47 merged and cleaning up the worktree.
