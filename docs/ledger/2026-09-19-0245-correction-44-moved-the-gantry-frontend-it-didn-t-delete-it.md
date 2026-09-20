# 2026-09-19 02:45 CDT - Correction: #44 moved the Gantry frontend, it didn't delete it
- **Who:** Session "Seven Of Nine".
- **Change:** Supersedes in part the 2026-09-19 02:10 entry. `apps/board/frontend` was **moved** to `frontend/` at the repo root by #44 (history preserved), not deleted. Today it differs from what Gantry runs by 6 files. Updated `docs/board/GANTRY_BUILD_SOURCE.md` to match.
- **Why:** Supersedes 2026-09-19-0210-gantry-build-source-identified-and-documented.md. The first diff compared `8d43b7a:apps/board` only against `legacy/board-express`, which missed the move.
- **State after:** Gantry is still unbuildable from `main` as-is (the compose files expect the old layout), but it's a small compose rewrite, not a code restore. Options memo: `C:\dev\GANTRY_FRONTEND_OPTIONS_2026-09-19.md` (recommends re-pointing the build at `main`).
- **Refs:** #44 table ("`frontend/` <- `apps/board/frontend`, history preserved").
