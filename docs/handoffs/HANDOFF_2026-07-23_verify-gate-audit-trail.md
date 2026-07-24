# HANDOFF 2026-07-23 -- verify-gate rework + persistent verify audit trail

Session goal (Drew): "get our verify system working... fix the issue and
reimplement or find a better way... consistent auditing, maybe audit notes we
could reference later." Root-caused the 2026-07-16 "everything is stuck"
incident, reworked the verify workflow, and built the audit trail. Work done
in worktree `C:\dev\paradigm-agilecards-wt-verify-gate` (branch
`fix/verify-gate-audit-trail`), per memory `parallel-chunks-share-checkout`.

## What this session did

- **Root cause (full reconstruction in `docs/adr/ADR-2026-07-23-verify-gate-and-audit-trail.md`):**
  1. The stuck state was NOT deep-verify. A legacy v1 branch-protection rule
     (fingerprint matches paradigm-platform `scripts/apply-tier3-gates.ps1`:
     `strict: true`, linear history, 0 reviewers) was layered over the
     ADR-2026-06-19 ruleset. GitHub enforces the union; `strict: true` made
     every open PR `BEHIND` whenever anything merged -- observed live on PRs
     #54/#55/#56 (all checks green, all BEHIND).
  2. Deep-verify never executed once (label-only trigger nothing applied +
     `needs:` chain behind a quick-verify that always skips on placeholder
     `deploy_url` + not a required check). Every "Paradigm Verify" PR run was
     8-18s of detect-then-skip.
  3. `deep_gate.sh` v1 accepted any stale PASS report forever.
- **Rewrote `.github/workflows/verify.yml`:** new `classify` job (tier-3 by
  `verify/tier3_paths.txt` regexes OR `tier-3` label; label promotes, never
  demotes); deep-verify decoupled from quick-verify; new **`verify-gate`**
  job (`if: always()`, aggregates results, always reports -- the ONLY context
  to mark Required); `timeout-minutes` on every job here and in `ci.yml`.
- **Audit trail (reuses platform DEC-GOV-001 pattern):** every verify run
  appends fenced JSON (`paradigm.verify-audit/v1`) to pinned issue "Verify
  Audit Log (paradigm.verify-audit/v1)" (auto-created), upserts a sticky PR
  comment with verdict + reasons, and writes the job summary. Skips are
  recorded WITH reasons, not painted green.
- **`verify/ci/deep_gate.sh` v2:** report must carry `Verified-Commit: <sha>`
  matching a commit of the PR under review (kills stale-report reuse).
  Template: `verify/REPORT_TEMPLATE.md`. Functionally tested against PR #58
  data (pass + stale-fail paths).
- **`verify/ci/apply_branch_protection.ps1`:** idempotent convergence -- 5
  required contexts (4 CI batteries + verify-gate), linear history, repo-admin
  bypass (Drew's escape hatch), `strict` OFF, deletes the v1 layer. NOT run
  yet (see below).
- Docs: ADR, `DECISIONS.md` entry, `verify/README.md`, `AUTO_MERGE.md`
  pointer, `ci.yml` header.

## What is currently broken or incomplete

- **The PR is open, not merged** (Tier-3 discipline: Drew merges).
- **Branch protection NOT yet converged.** The v1/`strict:true` layer is
  still live, so PRs #54/#55/#56 are still BEHIND-blocked. Sequencing is
  deliberate: run `verify/ci/apply_branch_protection.ps1` only AFTER this PR
  merges, then update open PRs so their branches contain the verify-gate job.
- **Platform-side hazard unfixed:** paradigm-platform
  `scripts/apply-tier3-gates.ps1` still PUTs v1 `strict:true` protection and
  would recreate the incident if re-run against this repo.
- portal-shell has the same family of flaws in its older verify.yml copy
  (placeholder = silent green pass in `quick_smoke.sh`; `redirects_to` /
  `json_path_equals` assertions silently skipped; same stale-report gate).

## What the next session should do first

1. If Drew approved: merge the PR (bottom-up if stacked; it is not).
2. Run `pwsh verify/ci/apply_branch_protection.ps1 -WhatIfMode`, review, then
   without `-WhatIfMode`. Verify with the script's built-in checks.
3. Update open PRs #54/#55/#56 (merge main in) so verify-gate reports on
   them; they stop being BEHIND-blocked once v1 is deleted.
4. Watch the first PR after merge: confirm verify-gate reports, the audit
   issue is created, and the sticky comment appears.
5. Port the fixes to portal-shell (separate session/PR).

## Open questions for Drew

- Fix `apply-tier3-gates.ps1` in paradigm-platform (make it ruleset-based,
  strict OFF) or retire it in favor of per-repo scripts?
- Is `backend/cards_api/` the right tier-3 scope once K11 (#47) lands, or
  should all of `backend/` promote then?

## Pointers

- ADR: `docs/adr/ADR-2026-07-23-verify-gate-and-audit-trail.md`
- Platform audit-log pattern reused: paradigm-platform
  `.github/workflows/pr-audit-log.yml`, `docs/governance/pr-audit-log.md`
- Tier-3 policy rationale: paradigm-platform `docs/tier3-merge-policy.md`
- Prior handoff: `docs/handoffs/HANDOFF_2026-06-30_k16-contract-tests.md`

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md` in this
project (still none at repo root -- consider adding one), then this file, then
run `vstart`. Do not run blanket git operations on the shared checkout
`C:\dev\paradigm-agilecards`; use a worktree.
