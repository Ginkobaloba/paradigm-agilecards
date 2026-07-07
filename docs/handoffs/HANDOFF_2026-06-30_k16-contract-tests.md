# HANDOFF 2026-06-30 -- K16: consumer contract tests (CARDS-013, CARDS-014)

Executed **CHUNK K16** (T2; owns CARDS-013, CARDS-014) per
`C:\dev\PARADIGM_INTEGRATION_ROADMAP.md`. Consumer contract tests in
`paradigm-agilecards` against the `@paradigm/*` platform packages, run on every
PR and every Renovate `@paradigm/*` bump.

## What this session did

- **CARDS-013 -- `@paradigm/auth` (Python, `backend/contracts/`).**
  `@paradigm/auth` is TypeScript-only; the roadmap has no Python `@paradigm/auth`
  in v1 (the FastAPI backend verifies IdP JWTs directly with PyJWT). So CARDS-013
  is an executable contract pinning what any Paradigm Python consumer must
  enforce. `backend/contracts/test_paradigm_auth_contract.py` -- 9 tests, all
  green: RS256+kid+JWKS resolution; JWKS refresh on unknown kid (fetched exactly
  once); expired / tampered / HS256-confusion / `alg:none` / missing-kid /
  malformed rejection. Self-contained (in-memory RSA + JWKS + reference
  verifier). Deps in `backend/contracts/requirements.txt`, decoupled from
  `backend/pyproject.toml` on purpose (the backend app deps are owned by K11).
- **CARDS-014 -- `@paradigm/llm-client` (TS, `frontend/contracts/`).** Scaffold
  per the chunk. Contract mirrored from the **real** `@paradigm/llm-client@0.1.0`
  source (`paradigm-platform/packages/llm-client`): five-method frozen interface,
  9-field telemetry payload (8 required + optional `error_code`), provider
  selection rule, stable error codes. 4 active checks pass; a `describe.skip`
  LIVE conformance block (real package + mock fetch) is dormant until K11b. New
  `vitest.contracts.config.ts` + `test:contracts` script (node env, scoped to
  `contracts/`, independent of the src/ Boards suite).
- **CI:** new `contracts` job in `.github/workflows/ci.yml` runs both suites
  (self-installing deps) on every PR. Non-required context for now.
- **renovate.json:** `@paradigm/*` bumps run the contracts job and **never
  auto-merge** (Tier-3; K17 reshapes the full review gate).
- **verification/cards/CARDS-013.md + CARDS-014.md = PASS.**
- **PR #48** opened; **CI fully green** (contracts 22s; engine-runner, both
  backend batteries, board frontend, Socket all pass; Quick/Deep Verify skip as
  designed). https://github.com/Ginkobaloba/paradigm-agilecards/pull/48

## What is currently broken or incomplete

- **PR #48 not merged.** CI is green; merge is Drew's call (T2 app repo is
  CI-gated, required-review 0). I did not auto-merge.
- **CARDS-014 live conformance is intentionally dormant** (`describe.skip`),
  wired by K11b when the Node BFF + `@paradigm/llm-client` dependency land.

## Out-of-band finding: K11 residue in this shared working tree (NOT at risk)

This K16 session ran concurrently with a K11 session in the **same checkout**
(a known pattern -- see memory `parallel-chunks-share-checkout`). The K11 work
left untracked/modified files in `C:\dev\paradigm-agilecards`:

- `backend/cards_api/` (auth/config/deps/main/store), modified `backend/app.py`,
  `backend/pyproject.toml` (v1.0.0, `pyjwt[crypto]`, `cards_api` packaging,
  `infisical` extra), `.gitignore`, `backend/.env.example`, `.venv/`, caches, and
  test drafts `backend/tests/{conftest,test_auth_verify,test_config_secrets,
  test_endpoint_auth,test_org_isolation}.py`.

**Correction to an earlier read:** this is **NOT an at-risk sole copy.** It is
**residue of the already-open PR #47** (`feat/cards-k11-jwt-auth`, K11, OPEN, not
merged) -- verified: that remote branch contains the full `backend/cards_api/`.
So the untracked residue is safe to discard; the committed K11 work lives in #47.

**This K16 work deliberately did not touch any of it.** K16 avoids the shared
`backend/pyproject.toml` by housing its deps in `backend/contracts/requirements.txt`;
everything K16 committed was staged by explicit path. Still: do NOT run `vend`
or `git add -A` on this shared checkout -- a blanket commit would sweep K11
residue into the wrong PR.

## What the next session should do first

1. **Use a git worktree for the next chunk** (per memory
   `parallel-chunks-share-checkout`). The K11 residue in this checkout can be
   discarded safely (`git checkout -- backend/app.py backend/pyproject.toml
   .gitignore` and remove the untracked `cards_api/`/drafts) -- it is reproduced
   in PR #47. Do NOT `vend`/`git add -A` here.
2. **Merge PR #48** if approved (CI is green). Bottom-up if stacked behind #47.
3. **K11 / K11b:** when the real verifier lands it must satisfy
   `backend/contracts/test_paradigm_auth_contract.py`. When the Node BFF +
   `@paradigm/llm-client` land, wire the CARDS-014 live block (flip
   `describe.skip` -> `describe`; add the package). See `frontend/contracts/README.md`.
4. **Promote `contracts` to a required status check** in branch protection once
   it has baked (it is additive/non-required today).

## Open questions for Drew

- Parallel chunks share one checkout (K11 + K16 here; documented in memory). The
  dispatcher does not hand out worktrees. Worth wiring worktree isolation into
  the chunk-dispatch flow so this stops recurring?
- PR #47 (K11) and PR #48 (K16) are both open against `main`. Merge order /
  whether #48 should rebase after #47 lands (both touch `backend/`, but disjoint
  paths -- `cards_api/` vs `contracts/` -- so no conflict expected).

## Pointers

- Roadmap: `C:\dev\PARADIGM_INTEGRATION_ROADMAP.md` (K16 = line ~300)
- Contract sources of truth: `C:\dev\paradigm-platform\packages\llm-client\src\{types,errors}.ts`
- PR #48: https://github.com/Ginkobaloba/paradigm-agilecards/pull/48
- Verification: `verification/cards/CARDS-013.md`, `CARDS-014.md`
- Prior handoff: `docs/handoffs/HANDOFF_2026-06-30_k2-paradigm-agilecards-rename.md`

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md` in this
project (there is none at repo root yet -- consider adding one), then this file,
then run `vstart`. First action: build the next chunk in a git worktree off
`origin/main`; the K11 residue in this shared checkout is safe to discard (it is
in PR #47). Do not run blanket git operations on this checkout.
