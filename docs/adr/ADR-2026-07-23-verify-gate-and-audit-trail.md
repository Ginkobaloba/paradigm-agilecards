# ADR-2026-07-23: verify-gate required check + persistent verify audit trail

**Status:** Proposed (accepted on merge of the PR carrying it)
**Date:** 2026-07-23
**Author:** Drew Mattick (via automation agent)
**Supersedes nothing; extends:** ADR-2026-06-19 (Rulesets v2 migration)

---

## Context: why "everything was stuck," with evidence

Drew disabled the strict check requirement on main on 2026-07-16 because PRs
stopped merging and the verify system was blamed. Reconstruction from CI
history, the live protection state, and the K17 tooling shows three distinct
facts, none of which is "deep-verify was slow":

### 1. The stuck state was a protection-layer collision, not a slow check

ADR-2026-06-19 migrated this repo to a single Ruleset (`main-branch-protection`,
id 17880692) and deleted the legacy v1 branch-protection rule. Later, a v1
rule REAPPEARED (fingerprint -- `strict: true`, `required_linear_history: true`,
0 approvals -- matches what paradigm-platform's `scripts/apply-tier3-gates.ps1`
PUTs; that K17 script targets this repo by default and predates the ruleset
migration; exact invocation unrecorded). GitHub enforces the UNION of a v1
rule and a ruleset. The union included v1's `strict: true` ("require branches
up to date before merging") -- a setting ADR-2026-06-19 had explicitly
rejected.

With `strict: true` and several agent-opened PRs in flight, every merge flips
every other open PR to `mergeState: BEHIND`, GitHub auto-merge will not
update branches on its own, the engine's merge gate parks cards in `blocked`
waiting for merges that never come, and the whole pipeline reads as "stuck."
As of 2026-07-23, PRs #54/#55/#56 sat green-on-every-check and BEHIND --
the mechanism, observed live.

### 2. Deep-verify never ran even once, so it could not have been slow or flaky

`Paradigm Verify` has completed in 8-18 seconds on every PR since 2026-06-27.
Three stacked conditions kept deep-verify inert:
  - it required a `tier-3` label that no PR has ever carried and nothing applied;
  - it was chained `needs: quick-verify`, and quick-verify always skipped
    because `verify/smoke.yml` `deploy_url` is a placeholder pending the
    reverted Gantry cutover -- a skipped `needs` dependency skips the
    dependent job too;
  - neither job was a required status check, so nothing noticed.
The workflow's only failure ever (2026-06-26) was DNS resolution against the
dead `app.projectnexuscode.org` host, which is what motivated the placeholder
guard.

### 3. The deep gate was also unsound when it did fire

`verify/ci/deep_gate.sh` v1 accepted ANY committed report in `verify/reports/`
containing a PASS marker -- one stale report would satisfy the gate for every
future tier-3 PR forever. (Same flaw exists in portal-shell's copy.)

## Decision

1. **One always-reporting required context: `verify-gate`.** The verify
   workflow gains a final job that runs `if: always()`, aggregates
   detect/classify/quick/deep results, and passes or fails with recorded
   reasons. Only `verify-gate` is marked Required. Conditional jobs are never
   required directly: a required context must be one that always reports,
   otherwise skips and renames strand PRs (the June AND July incidents were
   both "required context that never reports" in different costumes).
2. **Deep-verify is decoupled from the placeholder.** It depends on a new
   `classify` job (tier-3 = `tier-3` label OR touched paths in
   `verify/tier3_paths.txt`, the machine-readable AUTO_MERGE.md sensitivity
   list), not on quick-verify. The placeholder deploy_url now only skips the
   live smoke, and the skip is recorded as a reason, not painted green.
3. **The deep gate pins evidence to the PR.** deep_gate.sh v2 requires the
   committed report's `Verified-Commit` to be a commit of the PR under
   review. Commits pushed after the report are accepted residual risk,
   visible in the audit note via head_sha.
4. **Every job gets `timeout-minutes`.** Nothing in the verify or CI
   workflows may hold the 6-hour Actions default; a future hang fails loud.
5. **Single protection layer, admin escape hatch, strict stays OFF.**
   `verify/ci/apply_branch_protection.ps1` (run AFTER this PR merges)
   converges the ruleset to 5 required contexts (4 CI batteries +
   verify-gate), adds `required_linear_history` (preserving the one thing the
   v1 layer added that we want), adds a Repository-admin bypass actor (Drew's
   escape hatch so a false-positive gate can never strand him), and DELETES
   the v1 layer. `strict` remains false.
6. **Persistent audit trail, reusing DEC-GOV-001's pattern.** Every verify
   run appends a fenced-JSON entry (schema `paradigm.verify-audit/v1`) to the
   pinned append-only issue "Verify Audit Log (paradigm.verify-audit/v1)" --
   the app-repo sibling of paradigm-platform's PR Audit Log (AC-CI-008) --
   and upserts a human-readable sticky comment on the PR. What ran, what
   skipped, and WHY are recorded per run, referenceable forever, not just a
   green/red badge.

## Consequences

**Positive:** the gate actually gates; skips are honest and audited; a hang
times out; a stale report can't satisfy tier-3; one protection layer, one
place to look; Drew can always get out.

**Negative / accepted risks:**
- `verify/**` and `.github/workflows/**` are deliberately NOT tier-3 paths --
  making CI config demand a manual headed deep run is how the gate got turned
  off last time. Guard is required CI + review.
- The audit issue accumulates comments (one per verify run). Append-only is
  the point; GitHub issues handle thousands of comments.
- Open PRs predating the merge must take main into their branch before they
  can satisfy the new required context.
- The K17 script `apply-tier3-gates.ps1` in paradigm-platform still PUTs v1
  protection with `strict: true` and would recreate this incident if re-run
  against this repo. Flagged to Drew for a platform-side fix (out of scope
  here).

## Rollback

Remove `verify-gate` from the ruleset's required contexts (UI:
https://github.com/Ginkobaloba/paradigm-agilecards/settings/rules). The
workflow keeps running and auditing; it just stops blocking.
