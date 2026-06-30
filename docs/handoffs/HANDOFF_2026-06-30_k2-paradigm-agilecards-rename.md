# HANDOFF 2026-06-30 -- K2: consolidate/rename to paradigm-agilecards

Executed **CHUNK K2** of the Paradigm v1 build (T2; owns AC-CARDS-001,
AC-CARDS-002) per `C:\dev\PARADIGM_INTEGRATION_ROADMAP.md` and the council FPS.

## What this session did

- **Renamed the GitHub repo** `agile-cards` -> `paradigm-agilecards`
  (`gh repo rename`; GitHub redirect preserved, `delete_branch_on_merge` still
  on, origin remote auto-updated).
- **Restructured** from the interim `apps/{board,engine}` layout into the FPS
  target shape, history-preserving (`git mv`, 389 renames):
  - `frontend/` <- `apps/board/frontend` (React/Vite Boards UI)
  - `backend/`  = **new** FastAPI scaffold: `app.py` + `pyproject.toml` +
    `tests/test_healthz.py` (the passing CI placeholder). Scaffold only --
    the real Cards API is **K11**.
  - `engine/`   <- `apps/engine` (Python planner/runner; operator tool)
  - `legacy/board-express/` <- `apps/board/{backend,docker,scripts,compose,.env.example,README}`
    -- frozen pre-Paradigm Express/TS backend + its deploy, reference for
    **K10** (deploy) and **K11** (backend rewrite). **Delete after K11.**
  - `brand/`, `marketing/` <- `apps/board/{brand,marketing}`
  - `docs/board/` <- `apps/board/docs`; `tests/` repo-level placeholder (K16/V)
- **DECISIONS.md** local stub records the repo URL (AC-CARDS-001). The
  canonical platform `DECISIONS.md` is owned by **K18** (not yet created -- K1).
- **CI** (`.github/workflows/ci.yml`): repathed the three required batteries
  (job names unchanged so the required status-check contexts still report) and
  added a non-required `backend (fastapi scaffold)` job. `AUTO_MERGE.md` +
  `README.md` repathed.
- **Fixed a pre-existing date-rotted engine test** (`fix(engine): de-rot
  human-review wall test`). `test_pr_merged_metrics_records_diff_and_wall`
  hardcoded `merged_at="2026-06-30T00:00:00Z"` as a "future" date; on
  2026-06-30 the wall clock passed it, the wall clamped to `0.0`, and the
  engine battery (a REQUIRED check) went red for **every** PR. Now derives
  `merged_at` from `now + 1h`. The `> 0` assertion is unchanged. This was not
  caused by K2 -- K2 was just the first PR to run CI after the date rolled.
- **verification/cards/CARDS-001.md + CARDS-002.md = PASS** (audited: repo
  renamed, URL recorded, structure present, CI green).
- **PR #44** merged to `main` on green CI (squash). All 4 batteries + Socket
  Security green (CI run 28424040046).

## Merge-gate note (resolved)

At session start, `main` required 1 approving review on every PR -- which
collides with T2 auto-merge and is unself-approvable on a single account. By
the time CI was green, both the ruleset and classic protection showed
`required_approving_review_count: 0` (relaxed sometime during the session), so
`mergeStateStatus` was `CLEAN`. Drew authorized "relax review on this repo +
merge"; since review was already 0, the merge went through on green CI with
**no `--admin` bypass**. For an app repo this matches the FPS design (app repos
are CI-gated; human review is for the platform monorepo's `packages/` and
`@paradigm/*` bumps -- K17 / AC-CARDS-015).

## What is currently broken or incomplete

- **Local clone not yet moved** to `C:\dev\paradigm-agilecards`. Windows locks
  the directory while a process (this session's shell) has its CWD inside it,
  so the rename must run from a shell rooted elsewhere. Command in "next
  steps". The GitHub rename + origin remote are already done, so this is purely
  a local-folder cosmetic.
- **2 Dependabot alerts** (1 high, 1 moderate) on the default branch --
  pre-existing, out of K2 scope (dependency scanning is v1.5 / K-INF).
- **Live Gantry deploy paths moved** under `legacy/board-express/`. Running
  containers are unaffected; any rebuild/redeploy uses new paths and is a clean
  rewrite owned by **K10**. The Gantry cutover was already portal-blocked (see
  the 2026-06-25 handoff), so nothing operational regresses.
- **DECISIONS.md is a stub.** K18 must canonicalize in the platform monorepo.

## What the next session should do first

1. **Move the local clone** (run from a shell whose CWD is NOT inside the repo,
   e.g. open a fresh PowerShell at `C:\dev`):
   ```powershell
   # ensure no shell/editor/process has its CWD inside C:\dev\agile-cards
   Move-Item C:\dev\agile-cards C:\dev\paradigm-agilecards
   Set-Location C:\dev\paradigm-agilecards
   git remote -v   # already https://github.com/Ginkobaloba/paradigm-agilecards.git
   git fetch origin; git checkout main; git pull
   ```
2. **K1** -- stand up the platform monorepo (pnpm+turbo). **K18** -- canonicalize
   `DECISIONS.md` there and reduce this repo's stub to a pointer.
3. **K11** -- FastAPI backend (JWKS verify, org isolation, Infisical). Use
   `legacy/board-express/backend` as the rewrite reference, then delete it.
4. **K10** -- deploy `cards.paradigm.codes` (FastAPI + Node BFF + Boards); write
   the new compose (the legacy compose under `legacy/` is frozen, not the
   deploy source).

## Open questions for Drew

- **Branch protection on this app repo:** required-review is now 0 (matches the
  FPS app-repo design). Confirm intended, or wire the `@paradigm/*`-bump review
  rule (K17 / AC-CARDS-015) when packages exist.
- **marketing/ + brand/ placement:** keep in `paradigm-agilecards`, or migrate
  `marketing/` into the platform `apps/site` (K13)?

## Pointers

- Roadmap: `C:\dev\PARADIGM_INTEGRATION_ROADMAP.md`
- FPS: council Finished Product Specification (`paradigm-v1-fps.md`)
- PR #44: https://github.com/Ginkobaloba/paradigm-agilecards/pull/44
- Repo URL + structure decisions: `DECISIONS.md`
- Verification records: `verification/cards/CARDS-001.md`, `CARDS-002.md`
- Prior handoff: `docs/handoffs/HANDOFF_2026-06-25_gantry-portal-embed.md`

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md` in this
project (there is none at repo root yet -- consider adding one), then this
file, then run `vstart`. First action is the local-clone move above.
