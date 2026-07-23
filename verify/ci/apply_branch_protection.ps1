#requires -Version 5.1
<#
.SYNOPSIS
  Converges main branch protection for paradigm-agilecards to the
  ADR-2026-07-23 target state: ONE ruleset, five required contexts including
  verify-gate, and NO legacy v1 branch-protection rule.

.DESCRIPTION
  Policy-as-code, idempotent (re-running converges to the same state). The
  repo-local sibling of paradigm-platform's scripts/apply-tier3-gates.ps1,
  with one hard lesson applied: that script PUT a legacy v1 protection rule
  (strict: true) onto this repo while the ADR-2026-06-19 ruleset was already
  active. GitHub enforces the UNION of both layers, so `strict: true` made
  every open PR mergeState=BEHIND the moment anything else merged -- the
  "everything is stuck" livelock of 2026-07-16. This script deletes the v1
  layer and keeps the ruleset as the single source of truth.

  Target state (ruleset main-branch-protection):
    - no branch deletion, no force-push, linear history
    - PR required before merge, 0 approvals (solo-with-agents,
      docs/tier3-merge-policy.md in paradigm-platform: a review gate would
      deadlock Drew's own PRs)
    - required status checks (strict_required_status_checks_policy: false --
      "require branches up to date" stays OFF; it is the setting that caused
      the livelock and ADR-2026-06-19 already rejected it):
        engine runner battery (lint + tests)
        board frontend battery (lint + vitest)
        board backend battery (build + tests)
        backend (fastapi scaffold)
        verify-gate                      <- always-reporting, cannot strand a PR
    - bypass actor: Repository admin, always. This is Drew's escape hatch
      (the v1 rule's enforce_admins=false equivalent). Without it a
      false-positive verify-gate would block even Drew -- recreating the
      exact problem this work fixes.

  SEQUENCING -- run this AFTER the PR that adds the verify-gate job to
  .github/workflows/verify.yml has MERGED to main. Running it before makes
  verify-gate a required context that no open PR can report, stranding them.
  Open PRs created before the merge must be updated (merge main in / rebase)
  so their branch contains the verify-gate job before they can merge.

.EXAMPLE
  pwsh ./verify/ci/apply_branch_protection.ps1 -WhatIfMode   # print, don't apply
.EXAMPLE
  pwsh ./verify/ci/apply_branch_protection.ps1               # apply + verify
#>
[CmdletBinding()]
param(
  [string]$Owner     = 'Ginkobaloba',
  [string]$Repo      = 'paradigm-agilecards',
  [string]$Branch    = 'main',
  [long]  $RulesetId = 17880692,   # main-branch-protection (ADR-2026-06-19)
  [switch]$WhatIfMode
)

$ErrorActionPreference = 'Stop'
$full = "$Owner/$Repo"

try { gh auth status 2>$null | Out-Null }
catch { throw "gh CLI is not authenticated. Run: gh auth login" }

# ---- 1. converge the ruleset ---------------------------------------------
$body = [ordered]@{
  name        = 'main-branch-protection'
  target      = 'branch'
  enforcement = 'active'
  conditions  = [ordered]@{
    ref_name = [ordered]@{ include = @('~DEFAULT_BRANCH'); exclude = @() }
  }
  # Repository admin (actor_id 5) may bypass: Drew's escape hatch so a
  # false-positive gate can never strand him. Agents are not admins.
  bypass_actors = @(
    [ordered]@{ actor_id = 5; actor_type = 'RepositoryRole'; bypass_mode = 'always' }
  )
  rules = @(
    [ordered]@{ type = 'deletion' }
    [ordered]@{ type = 'non_fast_forward' }
    [ordered]@{ type = 'required_linear_history' }   # was only in the v1 layer; preserved here before v1 is deleted
    [ordered]@{
      type = 'pull_request'
      parameters = [ordered]@{
        required_approving_review_count = 0
        dismiss_stale_reviews_on_push   = $false
        require_code_owner_review       = $false
        require_last_push_approval      = $false
        required_review_thread_resolution = $false
        allowed_merge_methods           = @('merge', 'squash', 'rebase')
      }
    }
    [ordered]@{
      type = 'required_status_checks'
      parameters = [ordered]@{
        strict_required_status_checks_policy = $false   # NOT strict: strict is the 2026-07-16 livelock
        do_not_enforce_on_create             = $false
        required_status_checks = @(
          [ordered]@{ context = 'engine runner battery (lint + tests)' }
          [ordered]@{ context = 'board frontend battery (lint + vitest)' }
          [ordered]@{ context = 'board backend battery (build + tests)' }
          [ordered]@{ context = 'backend (fastapi scaffold)' }
          [ordered]@{ context = 'verify-gate' }
        )
      }
    }
  )
}
$json = $body | ConvertTo-Json -Depth 10
Write-Host "[$full] PUT rulesets/$RulesetId (5 required contexts incl. verify-gate; admin bypass; strict OFF)"
if ($WhatIfMode) { Write-Host $json } else {
  $json | gh api -X PUT "repos/$full/rulesets/$RulesetId" `
    -H "Accept: application/vnd.github+json" --input - | Out-Null
}

# ---- 2. delete the legacy v1 protection layer ----------------------------
$v1Exists = $true
try { gh api "repos/$full/branches/$Branch/protection" *> $null } catch { $v1Exists = $false }
if ($v1Exists) {
  Write-Host "[$full] DELETE branches/$Branch/protection (legacy v1 layer -- strict:true livelock source)"
  if (-not $WhatIfMode) {
    gh api -X DELETE "repos/$full/branches/$Branch/protection" | Out-Null
  }
} else {
  Write-Host "[$full] no legacy v1 protection present (already converged)"
}

# ---- 3. verify ------------------------------------------------------------
if (-not $WhatIfMode) {
  Write-Host ""
  Write-Host "Converged. Verification:"
  gh api "repos/$full/rulesets/$RulesetId" --jq '{name, enforcement, bypass: .bypass_actors, rules: [.rules[] | {type, checks: .parameters.required_status_checks, strict: .parameters.strict_required_status_checks_policy}]}'
  $v1Gone = $false
  try { gh api "repos/$full/branches/$Branch/protection" *> $null } catch { $v1Gone = $true }
  if ($v1Gone) { Write-Host "legacy v1 protection: ABSENT (correct)" }
  else { Write-Warning "legacy v1 protection STILL PRESENT -- delete failed, investigate" }
}
