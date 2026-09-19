# Ledger: paradigm-agilecards

Append-only record of what changed in this repo, why it changed, and where things
stand. Workspace-wide changes go in `C:\dev\LEDGER.md`. Convention:
`C:\dev\SESSION_PROTOCOL.md` section 13.

## Rules

- **Append only.** Add new entries at the bottom. Never edit or delete an old
  entry. To correct one, add a new entry that says `Supersedes <date> <title>`.
- **One entry per meaningful change:** a merge, deploy, migration, config or
  infra change, a decision, or a revert.
- **Record hard calls.** If you picked one option over a reasonable
  alternative, say what the alternative was and why it lost.
- **Mark unverified claims** as "(unverified)" or "(reported by <session>)".
- **Absolute dates and times.**

## Entry format

```
### YYYY-MM-DD HH:MM TZ · <short title>
- **Who:** <session / agent / Drew>
- **Change:** <what changed, concretely>
- **Why:** <the reason; for hard calls, the alternative and why it lost>
- **State after:** <what is true now; what is still open>
- **Refs:** <PRs, commits, files, docs>
```

## Entries

### 2026-09-19 02:10 CDT · Gantry build source identified and documented
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

### 2026-09-19 02:45 CDT · Correction: #44 moved the Gantry frontend, it didn't delete it
- **Who:** Session "Seven Of Nine".
- **Change:** Supersedes in part the 2026-09-19 02:10 entry. `apps/board/frontend` was **moved** to `frontend/` at the repo root by #44 (history preserved), not deleted. Today it differs from what Gantry runs by 6 files. Updated `docs/board/GANTRY_BUILD_SOURCE.md` to match.
- **Why:** The first diff compared `8d43b7a:apps/board` only against `legacy/board-express`, which missed the move.
- **State after:** Gantry is still unbuildable from `main` as-is (the compose files expect the old layout), but it's a small compose rewrite, not a code restore. Options memo: `C:\dev\GANTRY_FRONTEND_OPTIONS_2026-09-19.md` (recommends re-pointing the build at `main`).
- **Refs:** #44 table ("`frontend/` <- `apps/board/frontend`, history preserved").

### 2026-09-19 02:05 CDT · Gantry buildable from main (option B), not deployed
- **Who:** Subagent of the Orchestrator session, assignment "Option B" from `C:\dev\GANTRY_FRONTEND_OPTIONS_2026-09-19.md`.
- **Change:** Added `docker-compose.gantry.yml` at the repo root (context = repo root; frontend from `frontend/`, backend from `legacy/board-express/backend/`), path build args `BACKEND_DIR` / `FRONTEND_DIR` / `DOCKER_DIR` in `legacy/board-express/docker/Dockerfile.{backend,frontend}` (defaults keep the old layout), per-Dockerfile `.dockerignore` files, and `docs/board/GANTRY_DEPLOY.md`.
- **Why:** #44 split `apps/board`, and no compose file on main could build Gantry, so the Dependabot fixes on main never reached it. Hard calls:
  - **Repo-root file, not `legacy/board-express/docker-compose.gantry-main.yml`.** It composes two top-level trees and `legacy/` is slated for deletion after K11; the live recipe should not live in a doomed folder.
  - **One flattened file, not base + overlay.** The old base defines `cloudflared` with a required `TUNNEL_TOKEN`; Gantry never runs it.
  - **Build args, not copied Dockerfiles.** One Dockerfile per image to keep patched; the frozen `8d43b7a` recipe uses that commit's own Dockerfiles, so it stays valid.
  - **No `image:` and no explicit volume `name:`.** Both keep `-p board` resolving to the live names (`board-backend`, `board_board-data`) and keep a throwaway `-p` run from touching them.
  - **`PORTAL_JWKS_URL` moved to the new host; `PORTAL_ISSUER` left on the old-host string** (must equal the portal's configured issuer).
- **State after:** Rendered with `-p board`, the config differs from `8d43b7a:apps/board` only in build paths/args and `PORTAL_JWKS_URL`. Built and smoke-tested under throwaway project `gantry-verify-20260919` (ports 14070/18110, scratch cards, throwaway volume): `/gantry/` 200 with `/gantry/assets/` URLs, `/gantry/healthz` and backend `/healthz` 200, `/gantry/api/cards` 401 without a token. Torn down; live containers and images untouched. **Not deployed.** Portal token handoff through the new JWKS URL is unverified until a real deploy. Deploy needs Drew's go-ahead.
- **Refs:** PR #67 (build source doc, this ledger's first entries); #44; `docs/board/GANTRY_DEPLOY.md`.
