# Ledger: paradigm-agilecards

Append-only record of what changed, why it changed, and where things stand.
Any agent or human can read the newest entries and pick up the work without
asking.

## Rules

- **Append only.** Add new entries at the bottom. Never edit or delete an old
  entry. If an entry turns out to be wrong, add a new one that says
  `Supersedes <date> <title>` and explains the correction.
- **One entry per meaningful change:** a merge, deploy, migration, config or
  infra change, a decision, or a revert. Typo fixes don't need one.
- **Record hard calls.** If you picked one option over a reasonable
  alternative, write a sentence or two under **Why** so the next reader
  understands the choice.
- **State facts you checked.** Note anything you didn't verify, e.g.
  "(unverified)" or "(reported by <session>)".
- **Absolute dates and times** (e.g. `2026-09-17 09:37 CDT`), never "today".

## Entry format

```
### YYYY-MM-DD HH:MM TZ · <short title>
- **Who:** <session name / agent / Drew>
- **Change:** <what changed, concretely>
- **Why:** <the reason; for hard calls, the alternative and why it lost>
- **State after:** <what is true now; what is still open>
- **Refs:** <PRs, commits, files, docs>
```

## Entries

### 2026-09-19 02:30 CDT · Board (Chartroom) frontend and backend dependency security fixes
- **Who:** Claude session (Ginkobaloba, board-deps-security worktree)
- **Change:** Cleared all 25 open Dependabot alerts scoped to the live board app: 21 in frontend/package-lock.json, 4 in legacy/board-express/backend/package-lock.json. Frontend: bumped react-router-dom (pulls react-router 7.18.4, drops the vulnerable turbo-stream dependency entirely), vitest and @vitest/mocker to 4.1.11, postcss to 8.5.28, postcss-selector-parser to 6.1.4, browserslist to 4.29.0 and baseline-browser-mapping to 2.11.25, all via `npm audit fix` within existing semver ranges. Backend: bumped express 4.22.2 to 4.22.3 (transitively raises qs to 6.16.0 and body-parser to 1.20.8, closing both qs alerts without an override) and js-yaml to 4.3.2, via `npm audit fix`. No package.json ranges changed; only package-lock.json.
- **Why:** All fixes stayed inside the existing caret ranges already declared in package.json, so no major-version jump was needed for react, vite, or express. The qs alerts looked at first glance like they might need an `overrides` pin (express and body-parser both cap qs at `~6.15.1`), but express 4.22.3 relaxes its own qs range to `~6.16.0` and pulls body-parser 1.20.8 (which also requires `~6.16.0`), so the whole chain resolves naturally without an override.
- **State after:** `npm audit` (all severities, dev included) reports 0 vulnerabilities in both frontend and legacy/board-express/backend. Frontend typecheck, vitest unit suite (194 tests), vitest contracts suite, and `vite build` all pass. Backend typecheck, `tsc` build, and the node:test suite (96 tests) all pass. This entry and file are new; main did not have docs/LEDGER.md before this branch, and PR #67 (docs/gantry-build-source) and PR #68 (feat/gantry-build-from-main) both also add this file independently, so a merge-order conflict on this file is expected and trivially resolved by appending entries.
- **Refs:** PR (fix/board-deps-security-2026-09-19), supersedes stale Dependabot PR #65 (dependabot/npm_and_yarn/frontend/npm_and_yarn-d4d9e16cf3, state BEHIND). Does not touch #68's Dockerfiles/compose files or #67's docs.
