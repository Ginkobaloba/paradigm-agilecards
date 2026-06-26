# paradigm-agilecards

The Paradigm AgileCards app repo. AgileCards is the planning/runner engine and
Boards is its UI, so they live together here. This repo consumes the shared
`@paradigm/*` packages and deploys at `cards.paradigm.codes` (backend at
`/api`, Boards UI at `/`).

Licensed under PolyForm Noncommercial 1.0.0. See [LICENSE](./LICENSE).

> **Renamed from `agile-cards` on 2026-06-30 (chunk K2).** GitHub keeps a
> redirect from the old name. This chunk also collapsed the interim
> `apps/board` + `apps/engine` layout into the Paradigm target shape
> (`backend/` + `frontend/` + `tests/` + `docs/`). See [DECISIONS.md](./DECISIONS.md).

## Repo layout

```
paradigm-agilecards/
  backend/                FastAPI (Python). K2 scaffold; real Cards API is K11.
    app.py                FastAPI app with /healthz
    pyproject.toml        deps + dev extras (pytest, httpx, ruff)
    tests/                /healthz smoke test (the passing CI placeholder)
  frontend/               Vite + React + TypeScript + Tailwind Boards UI
  tests/                  repo-level integration/e2e home (placeholder; K16/V)
  docs/                   repo docs
    adr/                  architecture decision records
    handoffs/             session handoffs (see C:\dev\SESSION_PROTOCOL.md)
    board/                board-scoped docs (roadmap, tunnel, rules)
  engine/                 the planner skill + runner suite (Python). Operator tool.
    runner/               the runner package: lint + pytest gate
    lib/verifier/         the verifier the runner consumes
    templates/ examples/  card and manifest templates, a sample card
    SKILL.md RUNNER_CONTRACT.md DEFINITION_OF_DONE.md
  brand/                  Gantry brand assets (logotype, motion, tokens, preset)
  marketing/              Gantry marketing site (Vite). May fold into apps/site (K13).
  legacy/board-express/   frozen pre-Paradigm Express/TS backend + Docker/compose.
                          Reference for K10 (deploy) and K11 (backend rewrite). Delete after K11.
  verification/cards/     AC verification records (CARDS-001, CARDS-002, ...)
  DECISIONS.md            local stub recording the repo URL (K18 canonicalizes)
  .github/workflows/ci.yml  four-job CI (engine, board frontend, legacy board backend, fastapi)
  LICENSE  README.md
```

## CI

`.github/workflows/ci.yml` runs four jobs. The first three are required
status-check contexts on `main` (do not rename them):

- `engine runner battery (lint + tests)` -- ruff + pytest in `engine/runner`.
- `board frontend battery (lint + vitest)` -- Vitest in `frontend`.
- `board backend battery (build + tests)` -- tsc + tests in `legacy/board-express/backend`.
- `backend (fastapi scaffold)` -- ruff + pytest in `backend` (new; not yet required).

## Quick start

### Backend (FastAPI scaffold)

```powershell
cd C:\dev\paradigm-agilecards\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
ruff check .
pytest -q
uvicorn app:app --reload   # http://127.0.0.1:8000/healthz
```

The real Cards API (JWKS verify, org isolation, Infisical secrets) is owned by
**chunk K11**. See [`backend/README.md`](./backend/README.md).

### Frontend (Boards UI)

```powershell
cd C:\dev\paradigm-agilecards\frontend
npm install
npm run dev        # http://localhost:5173
npm test -- --run
```

### Engine (the runner suite)

```powershell
cd C:\dev\paradigm-agilecards\engine\runner
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
ruff check src tests
pytest -q
```

The full planning skill is documented in [`engine/SKILL.md`](./engine/SKILL.md);
the runner-side contract is in [`engine/RUNNER_CONTRACT.md`](./engine/RUNNER_CONTRACT.md).

## The runtime data folder

The engine and the legacy board both read/write a runtime data folder that is
intentionally outside the repo (default `C:\dev\todo\`, configurable per
project via `<project>\.cards-config.yaml`). Card files are the source of
truth; the board's SQLite DB is rebuildable dashboard-only state. Nothing in
`C:\dev\todo\` belongs in version control.

## How the halves talk

Process boundaries, not source imports. The board backend watches the card
tree on disk (`chokidar`) and serves a snapshot plus an SSE feed to the
frontend. The engine writes to that same tree from the runner's worktree
workers. The shared vocabulary they must agree on:

- the card frontmatter schema (`engine/templates/card.md`)
- the batch manifest schema (`engine/templates/batch_manifest.yaml`)
- the points-to-tier mapping (`engine/tier_map_claude.yaml`)
- the per-million pricing table (`engine/tier_pricing.yaml`)

Changes to those four files are the likeliest source of engine-vs-board drift,
so they are pinned in `.github/AUTO_MERGE.md` as Tier-3 sensitive.

## History

- 2026-06-18: `Ginkobaloba/agile-cards` (engine) and `Ginkobaloba/agile-cards-board`
  (board, grafted via `git subtree add --prefix=apps/board`) were merged into one
  monorepo under `apps/`. History from both survives.
- 2026-06-30 (chunk K2): renamed to `paradigm-agilecards` and restructured from
  `apps/{board,engine}` to the Paradigm target shape. `git log --follow` reaches
  the original commits through the renames.

## Verification

The `verify/` directory contains the Paradigm Verify suite for this repo.
It covers the board's live surfaces and the card REST API.

### Quick reference

| Command | What it does |
|---------|-------------|
| `/verify C:\dev\paradigm-agilecards` | Quick smoke against `verify/smoke.yml` (all PRs, ~10s) |
| `/verify deep C:\dev\paradigm-agilecards` | Full deep verify including browser layers (Tier-3 PRs, local only) |

### Surfaces and tiers

| Surface | Tier | Gate |
|---------|------|------|
| home (TokenGate form) | 1 | smoke only |
| healthz | 1 | smoke only |
| SSE events stream | 2 | smoke; deep recommended |
| GET /api/cards (read) | 2 | smoke; deep recommended on parser changes |
| POST /api/cards/:id/move | **3** | deep-verify required before merge |
| PATCH /api/cards/:id/frontmatter | **3** | deep-verify required before merge |
| auth token gate (requireAuth) | **3** | deep-verify required before merge |

### CI behavior

`.github/workflows/verify.yml` runs two jobs on every PR:

- `quick-verify` (all PRs): runs `verify/ci/quick_smoke.sh` against
  `verify/smoke.yml`. Checks HTTP status, json_path_equals, and headers.
  Browser assertions (selector_present) are skipped; they run in the
  local deep pass.

- `deep-verify` (PRs labeled `tier-3` only): runs `verify/ci/deep_gate.sh`,
  which requires a committed report under `verify/reports/` containing
  `Overall: PASS`. A tier-3 PR with no report, or with a non-PASS report,
  fails the gate and cannot merge.

To make the `deep-verify` job actually block merges, add it as a required
status check in Settings > Branches for the `main` branch protection rule.

### Running locally

```powershell
# Quick smoke (same as CI, no token needed for the unauthed assertions)
bash verify/ci/quick_smoke.sh verify/smoke.yml

# Deep verify -- requires computer-use MCP + Chrome + a valid BOARD_TOKEN
# Follow prompts from the /verify deep skill run; commit the report to
# verify/reports/YYYY-MM-DD-<surface>-deep.md with "Overall: PASS".
```

The deploy URL is `https://app.projectnexuscode.org` (Cloudflare Tunnel,
Cloudflare Access gated). Update `verify/smoke.yml` if the URL changes.
