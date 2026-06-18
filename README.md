# agile-cards (monorepo)

One product, two halves. The planner and runner suite that turns a story
into independently claimable cards lives at [`apps/engine/`](./apps/engine).
The web dashboard that shows a fleet of agents working those cards in real
time lives at [`apps/board/`](./apps/board). They were built simultaneously
in separate repos, which made the 1:1 coupling between them harder to keep
honest. They live here now.

Licensed under PolyForm Noncommercial 1.0.0. See [LICENSE](./LICENSE).

## Why a monorepo

The engine and the board move together. The engine's card schema is the
board's view model. The engine's ledger feeds the board's metrics
surfaces. A schema change without a matching board change is a bug, and
catching that as a PR-level review (instead of a cross-repo deploy
surprise) is the whole point of pulling them under one roof.

Two ground rules that fall out of this:

1. A change to the card store schema (`apps/engine/runner/src/cards_runner/store/`)
   should land in the same PR as the matching board updates
   (`apps/board/backend/src/`, `apps/board/frontend/src/state/`). The
   auto-merge policy now lists those board surfaces in its Tier-3
   sensitivity set so the guard trips correctly.
2. CI gates both apps on every PR. See `.github/workflows/ci.yml` for the
   three jobs: `engine-runner`, `board-frontend`, `board-backend`.

## Repo layout

```
agile-cards/
  apps/
    engine/                 the planner skill + runner suite (Python)
      runner/               the runner package: lint + pytest gate
      lib/verifier/         the verifier the runner consumes
      templates/            card and manifest templates
      examples/             a sample card the planner emits
      docs/                 engine-scoped docs (design notes, handoffs, audits)
      dashboard-v0/         the single-file HTML prototype, kept for archaeology
      DEFINITION_OF_DONE.md merge-gate contract
      RUNNER_CONTRACT.md    the runner-side contract
      SKILL.md              the full planning skill spec
      tier_map_claude.yaml  canonical points-to-model map (planner + runner read this)
      tier_pricing.yaml     canonical per-million USD rates (cost governor reads this)
      README.md             engine-scoped README
    board/                  the web dashboard
      frontend/             Vite + React + TypeScript + Tailwind + @dnd-kit + Zustand
      backend/              Express + TypeScript + better-sqlite3 + chokidar + SSE
      docker/               Dockerfiles + nginx.conf for the production-style run
      docs/                 board-scoped docs (roadmap, tunnel, rules)
      scripts/              PowerShell helpers for branch / PR housekeeping
      docker-compose.yml    all-in-one local stack
      setup.ps1             one-shot dev setup
      README.md             board-scoped README
  .github/
    workflows/ci.yml        three-job CI (engine + board frontend + board backend)
    AUTO_MERGE.md           the auto-merge policy, updated for the monorepo paths
  .gitignore                merged from both prior repos
  LICENSE                   PolyForm Noncommercial 1.0.0, applies to the whole tree
  README.md                 you are here
```

## Quick start

### Engine (the runner suite)

```powershell
cd C:\dev\agile-cards\apps\engine\runner
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]

# Lint + test (the CI gate runs the same thing)
ruff check src tests
pytest -q
```

The full planning skill is documented in
[`apps/engine/SKILL.md`](./apps/engine/SKILL.md). The runner-side contract
is in [`apps/engine/RUNNER_CONTRACT.md`](./apps/engine/RUNNER_CONTRACT.md).

### Board (the dashboard)

You need Node 20+ and npm. Docker is optional for an all-in-one run.

```powershell
# Backend
cd C:\dev\agile-cards\apps\board\backend
npm install
$env:CARDS_DIR = "C:\dev\todo"
$env:DB_PATH   = "C:\dev\agile-cards\apps\board\backend\data\board.sqlite"
$env:PORT      = "4070"
npm run create-token -- --label "drew-laptop"   # save the token
npm run dev                                      # http://localhost:4070

# Frontend (separate terminal)
cd C:\dev\agile-cards\apps\board\frontend
npm install
npm run dev                                      # http://localhost:5173
```

For the Docker path and the tunneled hosted setup, see
[`apps/board/README.md`](./apps/board/README.md) and
[`apps/board/docs/PERSISTENT_TUNNEL.md`](./apps/board/docs/PERSISTENT_TUNNEL.md).

## The runtime data folder

The engine and the board both read from and write to a runtime data
folder that is intentionally outside the repo. Default location:

```
C:\dev\todo\
  backlog\     cards waiting to be claimed
  active\      cards an executor is working
  amendments\  cards awaiting human review of an AC change
  done\        cards whose work merged
  blocked\     cards finished but unmerged or paused on deps
  _batches\    per-batch manifest files
```

The path is configurable per project via `<project>\.cards-config.yaml`.
Nothing in `C:\dev\todo\` belongs in version control. The card files
are the source of truth; the board's SQLite database is dashboard-only
state (auth tokens, sprint scheduling, retro history, per-user prefs)
and is rebuildable from the card tree.

## How they talk to each other

Process boundaries, not source imports. The board's backend watches the
card tree on disk with `chokidar` and serves a snapshot plus an SSE feed
to the frontend. The engine writes to that same tree from the runner's
worktree workers. The two halves never share a process. The shared
vocabulary they have to agree on is:

- the card frontmatter schema (`apps/engine/templates/card.md`)
- the batch manifest schema (`apps/engine/templates/batch_manifest.yaml`)
- the points-to-tier mapping (`apps/engine/tier_map_claude.yaml`)
- the per-million pricing table (`apps/engine/tier_pricing.yaml`)

Any change to those four files is the most likely source of an
engine-vs-board drift, so they are pinned in `.github/AUTO_MERGE.md` as
Tier-3 sensitive.

## History note

This monorepo was created on 2026-06-18 by merging two previously
independent repos:

- `Ginkobaloba/agile-cards` (86 commits) provided the canonical home
  and is now `apps/engine/`.
- `Ginkobaloba/agile-cards-board` (51 commits) was grafted in via
  `git subtree add --prefix=apps/board` so the original commit history
  survives. `git log apps/board/<file>` reaches the original board
  commits with their original SHAs and authors. `git log apps/engine/<file> --follow`
  reaches the engine's original commits.

The standalone `agile-cards-board` repo is archived. Its main branch
carries a `SUPERSEDED.md` and an `archived-pre-monorepo-2026-06-18` tag
pointing at this monorepo.
