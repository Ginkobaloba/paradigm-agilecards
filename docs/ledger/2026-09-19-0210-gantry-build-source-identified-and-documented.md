# 2026-09-19 02:10 CDT - Gantry build source identified and documented
- **Who:** Session "Seven Of Nine", on an Orchestrator assignment (gap list item 2).
- **Change:** Added `docs/board/GANTRY_BUILD_SOURCE.md` and this ledger.
- **Why:** The live `gantry-board-*` containers recorded the working dir
  `C:\dev\agile-cards\apps\board`, which no longer exists, and #44 deleted
  `frontend/` from `legacy/board-express`. Production Gantry could not be
  rebuilt from any documented path. Rendering commit `8d43b7a` (`apps/board`,
  both compose files, project `board`) reproduces both live
  `com.docker.compose.config-hash` labels exactly, which proves the source.
  - **Choice: document the pinned commit, don't restore `frontend/` on main.**
    Restoring it would put 21k lines of legacy code back under dependency bumps
    and CI, and moving Gantry to the new frontend is a product decision. A
    pinned, hash-verified recipe removes the "can't rebuild" risk today and
    leaves that decision to Drew.
- **State after:** Gantry is rebuildable from `8d43b7a` using the recipe in the
  doc. Nothing was rebuilt or restarted. Open: the restore-vs-migrate decision
  for the Gantry frontend; `PORTAL_JWKS_URL` still points at the old portal host.
- **Refs:** commit `8d43b7a` (#42); restructure #44; `C:\dev\PROJECTS_100_GAP_2026-09-19.md` item 2.
