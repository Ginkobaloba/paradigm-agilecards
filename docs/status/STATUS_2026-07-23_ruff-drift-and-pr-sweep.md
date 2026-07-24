---
schema: paradigm-status/v1
repo: paradigm-agilecards
project: agilecards
task: "PR-cleanup sweep + remediate the ruff 0.16.0 CI drift that reddened the required gate repo-wide"
date: 2026-07-23
author: Claude Opus 4.8 (1M context)
session: "opus-pr-sweep-2026-07-23"
state: green
phase: shipped
summary: >-
  ruff 0.16.0 shipped 2026-07-23 and enabled the isort rule I001 (plus C408/B008) by
  default; the unpinned `ruff>=0.5` dev spec pulled it in and reddened the required
  engine + backend ruff jobs on unchanged code, so every open PR was blocked and main
  was stale-green. Pinned ruff==0.15.22 (PR #62), which restored a genuinely green main
  and unblocked the strand. Cleared the clean docs backlog (#54, #56) and rebased the
  verify-gate PR (#59) onto the pin so its previously-stale green is now real; #59 stays
  Drew-gated. Next: merge #59, and consider a lockfile so pytest/mypy/anthropic can't
  drift next.
shipped:
  - item: "Pin ruff==0.15.22 in all three CI install points (engine/runner + backend pyproject dev extras, contracts/requirements.txt)"
    pr: "https://github.com/Ginkobaloba/paradigm-agilecards/pull/62"
    verified: "Local repro on Python 3.11 (0.16.0 -> 198 engine + 13 backend errors; 0.15.22 -> all pass) AND PR CI: all 4 required checks green; post-merge main CI run 30062833875 = success"
  - item: "Merge docs(audit): alpha-readiness audit + prioritized gap list (Tier-1, 1 md file)"
    pr: "https://github.com/Ginkobaloba/paradigm-agilecards/pull/54"
    verified: "Rebased onto pinned main; all 4 required checks green; squash-merged, branch auto-deleted"
  - item: "Merge docs(care-package): reconciliation + deep council + Fable brief (Tier-1, 9 md files)"
    pr: "https://github.com/Ginkobaloba/paradigm-agilecards/pull/56"
    verified: "Rebased onto pinned main; all 4 required checks green; squash-merged, branch auto-deleted"
in_progress:
  - item: "PR #59 (always-reporting verify-gate + audit trail) rebased onto pinned main, CI genuinely green"
    remaining: "Drew-gated merge (Tier-3: edits ci.yml/verify.yml/apply_branch_protection.ps1). Its earlier all-green was STALE (pre-0.16.0); the rebase makes it real so the gate change is not merged on a green that no longer holds."
blockers: []
decisions_needed:
  - question: "Adopt ruff 0.16's newly-default rules (import sorting I001, C408, B008) as a repo standard, or stay on 0.15.22?"
    options:
      - label: "Stay pinned at 0.15.22"
        pros: "Zero churn; preserves the repo's declared intent (it never selected isort); deterministic now"
        cons: "Frozen linter until a deliberate bump; misses newer ruff checks"
      - label: "Bump to 0.16.x + enable rules explicitly + ruff --fix the ~211 blocks"
        pros: "Modern ruleset, import hygiene enforced"
        cons: "~211-line mechanical churn; a new standard Drew didn't originally choose"
    recommendation: "Stay pinned now; revisit as a deliberate PR if import-sorting is wanted. Low urgency."
    urgency: whenever
next_steps:
  - action: "Drew merges #59 (verify-gate) now that its green is real"
    why: "Restores the always-reporting verify-gate so a future silent skip can't recur; it is the fix for the original incident that started this sweep"
    effort: "minutes"
    risk: low
  - action: "Add a lockfile (uv or pip-tools) for engine/runner + backend so pytest/mypy/anthropic can't drift like ruff did"
    why: "Pinning ruff fixed the fire; the same floating-spec class still applies to the other dev tools (mypy>=1.10, pytest>=8, anthropic>=0.40)"
    effort: "hours"
    risk: low
  - action: "Resolve the open correction comment on #55, then rebase onto pinned main and merge (docs)"
    why: "#55 content is fine but carries an unresolved note that its PR description overclaimed 'safe to prune' branches; the two branches it names hold ~5,600 lines of real RLS work and must NOT be deleted"
    effort: "minutes"
    risk: low
risks:
  - "#61 (frontend CardTile fix) is a DRAFT opened by a concurrent session ~20s before #62; it needs a rebase onto pinned main before its own green is real. Left untouched to avoid colliding with that session."
  - "Secondary drift: mypy>=1.10 in engine/runner is unpinned but runs continue-on-error, so it cannot gate today. Pin it when the lockfile lands."
  - "Cross-repo (out of this repo's scope, flagged for the fleet): paradigm-platform CI is structurally hollow (no lint/test/typecheck runs on PRs), and platform #28 is blocked by a broken pnpm-lock.yaml."
metrics:
  work_type: infrastructure
  tier: 2
  agent_minutes: null
  tokens_total: null
  cost_usd: null
  retries: null
  prs_opened: 1
  prs_merged: 3
links:
  prs:
    - "https://github.com/Ginkobaloba/paradigm-agilecards/pull/62"
    - "https://github.com/Ginkobaloba/paradigm-agilecards/pull/54"
    - "https://github.com/Ginkobaloba/paradigm-agilecards/pull/56"
    - "https://github.com/Ginkobaloba/paradigm-agilecards/pull/59"
  docs:
    - "docs/status/STATUS_2026-07-23_ruff-drift-and-pr-sweep.md"
  handoff: ""
---

## Detail

The sweep started as routine PR housekeeping and immediately hit a repo-wide red gate.

Root cause: **ruff 0.16.0 was released 2026-07-23** and changed its default rule set to
include import sorting (`I001`) plus `C408`/`B008`. Because CI installs ruff via the
unpinned `ruff>=0.5` dev spec (`pip install -e .[dev]`), the new release was pulled in
mid-afternoon and flagged ~200 previously-clean import blocks in `engine/runner` and 13 in
`backend` on **unchanged code**. That reddened the two required Python jobs (`engine runner
battery`, `backend (fastapi scaffold)`) for every open PR. Main itself had last run green on
2026-07-21 (pre-release) with no run since, so it was **stale-green**: recorded green, but a
re-run would fail. This is the exact "green that no longer holds" trap the verify-gate work
(#59) exists to surface.

Proof of the version boundary: #59's battery passed at 07:08 on ruff `0.15.22`; #54's re-run
at 21:58 failed on ruff `0.16.0` with the identical code. Reproduced locally on Python 3.11:
0.16.0 -> 198 engine + 13 backend errors; 0.15.22 -> all pass across engine, backend, and
contracts.

Fix: pin `ruff==0.15.22` in the three files CI installs from. Rationale over the alternative
(bump to 0.16 + `ruff --fix` the ~211 blocks): the repo never selected isort, so adopting it
is a new standard that should be a deliberate choice, not a side effect of an unpinned
upgrade. The pin restores the exact known-good behavior and makes CI deterministic.

A read-only fleet audit (9 active Paradigm repos, one agent each) confirmed the exposure is
**agilecards-only** -- every other repo is pure TS/Node with no ruff, so #62 remediates the
entire drift class. The audit also surfaced two items that were NOT in the original PR list:
#61 (a concurrent session's draft) and the fact that the "Nate Jones" client-facing PRs live
on `paradigm-site` (#65/#66/#67/#68), not `paradigm-platform` as briefed.

Merge order was bottom-up: #62 (pin) first to un-red the gate, then the clean docs PRs #54
and #56, each rebased onto the advancing main and re-verified green (strict branch protection
requires up-to-date branches). #59 was rebased onto the pin so its green is real, then left
for Drew (Tier-3, edits gate/branch-protection files). #55 is held on an open correction
comment. #61 (draft, another session) left untouched.

## Evidence

- ruff version boundary: run 29987330358 (0.15.22, pass) vs run 30048188409 (0.16.0, fail, `I001`).
- Local repro (Python 3.11.7): 0.16.0 -> `Found 198 errors` (engine) + `Found 13 errors` (backend); 0.15.22 -> `All checks passed!` on engine `src tests`, backend `.`, backend `contracts`.
- #62 PR CI: engine runner battery pass (1m37s), backend fastapi pass, board backend/frontend pass. Post-merge main CI run 30062833875 = success.
- #54 rebased CI run 30062871136: all 4 required pass. #56 rebased CI run 30063025239: all 4 required pass.
- Repo merge hygiene: `deleteBranchOnMerge=true`; all three PRs squash-merged with branch auto-delete.

## Notes for next session

- Read `PARADIGM_VELOCITY_RULES.md` lives at `C:\dev\PARADIGM_VELOCITY_RULES.md` (confirmed present).
- Do NOT delete branches `feat/cards-api-postgres-rls` or `feat/backend-postgres-rls` -- ~5,600 lines of real Postgres/RLS work (per the correction comment on #55).
- The repo has no `tier-1/2/3` or `held` labels defined, so the velocity-rules label conventions and the `tier-3`-gated Deep Verify cannot actually fire here; #59 is reshaping the verify wiring, so labels were left for that work rather than added mid-flight.
- lumen-analytics #24 (read-only MCP server) was merged as part of this sweep's approved scope; its local checkout has uncommitted concurrent-session work on `mcp/*` + `verify.yml` that must be reconciled onto main separately.
