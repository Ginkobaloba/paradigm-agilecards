# DECISIONS -- paradigm-agilecards (local stub)

> **Status: STUB.** The canonical, platform-wide `DECISIONS.md` lives in the
> Paradigm **platform monorepo** and is owned by chunk **K18**
> (AC-COMP-001/002/003: ATO posture, WorkOS trigger verbatim, full settled-
> decision log). That monorepo does not exist yet (chunk K1 creates it).
>
> This file exists so that **AC-CARDS-001** can be verified at the app-repo
> level (the repo URL is recorded next to the code) without blocking on K1/K18.
> When the platform monorepo's `DECISIONS.md` absorbs these entries, this file
> should be reduced to a pointer at the canonical one. **K18 canonicalizes.**

## Repository (AC-CARDS-001)

- **Name:** `paradigm-agilecards`
- **URL:** https://github.com/Ginkobaloba/paradigm-agilecards
- **Org:** Ginkobaloba (GitHub)
- **Renamed from:** `agile-cards` on 2026-06-30 (chunk K2). GitHub preserves a
  redirect from the old name, so existing clones/remotes keep working.
- **Visibility:** public. `delete_branch_on_merge`: enabled.

## Structure decisions (AC-CARDS-002, chunk K2)

The repo was collapsed from the interim `apps/board` + `apps/engine` monorepo
layout into the FPS target shape (`backend/` + `frontend/` + `tests/` +
`docs/` + CI). The FPS is authoritative and newer than the `apps/` layout.

| Path | Contents | Owner / fate |
|------|----------|--------------|
| `backend/` | FastAPI scaffold (`app.py`, `pyproject.toml`, `/healthz`) | Real Cards API is **K11**. Python/FastAPI locked (roadmap Q2). |
| `frontend/` | React/Vite Boards UI (moved from `apps/board/frontend`) | `@paradigm/ui` + `@paradigm/llm-client` wiring is **K11b**. |
| `tests/` | Repo-level integration/e2e placeholder | Filled by **K16** / **V**. |
| `docs/` | Repo docs; board docs under `docs/board/` | -- |
| `engine/` | Python planner/runner engine (moved from `apps/engine`) | Operator tool (roadmap section 8). |
| `legacy/board-express/` | Pre-Paradigm Express/TS backend + its Docker/compose | Frozen reference for **K10** (deploy) and **K11** (backend rewrite). **Delete after K11.** |
| `brand/`, `marketing/` | Gantry brand assets + marketing site | May fold into platform `apps/site` (**K13**). |

### Known consequence to flag

Moving `apps/board/docker-compose*.yml` and `apps/board/docker/` into
`legacy/board-express/` changes the paths the **live Gantry deploy** was built
from. Running containers are unaffected, but any rebuild/redeploy must use the
new paths -- and the FastAPI/Node-BFF deploy is a clean rewrite owned by
**K10** anyway. The Gantry cutover was already portal-blocked (see the latest
handoff), so nothing operational regresses here.

## Verify gate + audit trail (2026-07-23)

- **One always-reporting required context (`verify-gate`) gates merges; the
  legacy v1 protection layer is deleted; `strict` (require-up-to-date) stays
  OFF.** The 2026-07-16 "everything is stuck" incident was a v1 rule with
  `strict: true` layered over the ADR-2026-06-19 ruleset -- not a slow
  deep-verify (deep-verify had never executed). Full reconstruction and
  decision: `docs/adr/ADR-2026-07-23-verify-gate-and-audit-trail.md`.
- **Every verify run leaves a persistent audit note** (schema
  `paradigm.verify-audit/v1`) on the pinned issue "Verify Audit Log
  (paradigm.verify-audit/v1)" plus a sticky PR comment -- the app-repo
  sibling of the platform's DEC-GOV-001 PR Audit Log.
- **Tier-3 is path-based, not label-only:** `verify/tier3_paths.txt` +
  `verify/ci/classify_tier.sh`; the `tier-3` label remains as a manual
  promotion override. Deep-verify evidence must pin a `Verified-Commit`
  belonging to the PR (`verify/ci/deep_gate.sh` v2).
- Protection is converged by `verify/ci/apply_branch_protection.ps1`
  (idempotent; run after the carrying PR merged).

## Pointers

- Roadmap: `C:\dev\PARADIGM_INTEGRATION_ROADMAP.md`
- FPS (council Finished Product Specification): `paradigm-v1-fps.md`
- Canonical decisions (not yet created): platform monorepo `DECISIONS.md` (**K18**).
