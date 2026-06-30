<#
.SYNOPSIS
    Deletes the stalled non-canonical -v branches retired by the 2026-06-18 v3-halt sweep.

.DESCRIPTION
    Per the 2026-06-18 retro (C:\dev\_retros\RETRO_2026-06-18.md), four features on this
    repo had stalled v1+v2 or v1+v2+v3 patterns four weeks ago. The sweep selected the
    highest version of each as canonical and retired the rest. This script deletes the
    retired branches both locally and on origin.

    Run this from a clean Windows PowerShell session in C:\dev\agile-cards-board.
    Do NOT run it from WSL or any Linux sandbox; the retro flagged that pattern as the
    cause of half the repo corruption it found. SESSION_PROTOCOL.md section 7 applies.

.NOTES
    Canonical branches (KEEP, do not delete):
      - feature/card-event-timeline-v2
      - feature/cmdk-filter-views-v3
      - feature/manual-rank-v3
      - feature/tile-polish-v2

    Retired branches (this script deletes them):
      - feature/card-event-timeline
      - feature/cmdk-filter-views
      - feature/cmdk-filter-views-v2
      - feature/manual-rank
      - feature/manual-rank-v2
      - feature/tile-polish

    Background reading before you run this:
      - docs/handoffs/HANDOFF_card-event-timeline_v3-halt_2026-06-18.md
      - docs/handoffs/HANDOFF_cmdk-filter-views_v3-halt_2026-06-18.md
      - docs/handoffs/HANDOFF_manual-rank_v3-halt_2026-06-18.md
      - docs/handoffs/HANDOFF_tile-polish_v3-halt_2026-06-18.md
      - docs/RULES.md (the v3-means-halt rule)

.PARAMETER DryRun
    If passed, prints the git commands that would run without executing them.
    Use this on the first pass to sanity-check.

.PARAMETER SkipLocal
    Skip the local branch deletes. Use if you only want to clean up origin.

.PARAMETER SkipRemote
    Skip the origin branch deletes. Use if you want to keep the remotes for review.

.EXAMPLE
    .\scripts\delete-stalled-v-branches.ps1 -DryRun

.EXAMPLE
    .\scripts\delete-stalled-v-branches.ps1
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipLocal,
    [switch]$SkipRemote
)

$ErrorActionPreference = "Stop"

# Explicit list. Every branch named here gets deleted; everything not named here is left alone.
$RetiredBranches = @(
    "feature/card-event-timeline",
    "feature/cmdk-filter-views",
    "feature/cmdk-filter-views-v2",
    "feature/manual-rank",
    "feature/manual-rank-v2",
    "feature/tile-polish"
)

$CanonicalBranches = @(
    "feature/card-event-timeline-v2",
    "feature/cmdk-filter-views-v3",
    "feature/manual-rank-v3",
    "feature/tile-polish-v2"
)

Write-Host ""
Write-Host "v3-halt branch sweep, 2026-06-18" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Canonical branches (will NOT be touched):" -ForegroundColor Green
$CanonicalBranches | ForEach-Object { Write-Host "  + $_" -ForegroundColor Green }
Write-Host ""
Write-Host "Retired branches (will be deleted):" -ForegroundColor Yellow
$RetiredBranches | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
Write-Host ""

# Pre-flight: confirm we're in the agile-cards-board repo, on a sane branch, and origin points where we expect.
$repoRoot = (git rev-parse --show-toplevel 2>$null)
if (-not $repoRoot) {
    Write-Error "Not inside a git repo. cd into C:\dev\agile-cards-board first."
    exit 1
}
if (-not ($repoRoot -replace '/','\').EndsWith("agile-cards-board")) {
    Write-Error "Wrong repo. Expected agile-cards-board, got '$repoRoot'."
    exit 1
}

$originUrl = (git remote get-url origin 2>$null)
if (-not $originUrl) {
    Write-Error "No origin remote configured."
    exit 1
}
Write-Host "Repo: $repoRoot"
Write-Host "Origin: $originUrl"
Write-Host ""

# Confirm none of the canonical branches accidentally landed in the retired list.
$overlap = $CanonicalBranches | Where-Object { $RetiredBranches -contains $_ }
if ($overlap) {
    Write-Error "Sanity check failed: canonical branches present in retired list: $($overlap -join ', ')"
    exit 1
}

# Confirm with the operator before doing anything destructive (unless DryRun).
if (-not $DryRun) {
    Write-Host "About to delete the retired branches listed above." -ForegroundColor Yellow
    $response = Read-Host "Proceed? Type 'yes' to continue, anything else to abort"
    if ($response -ne "yes") {
        Write-Host "Aborted." -ForegroundColor Red
        exit 0
    }
}

# Local deletes.
if (-not $SkipLocal) {
    Write-Host ""
    Write-Host "--- Local branch deletes ---" -ForegroundColor Cyan
    foreach ($branch in $RetiredBranches) {
        $exists = git rev-parse --verify --quiet "refs/heads/$branch" 2>$null
        if ($exists) {
            $cmd = "git branch -D $branch"
            Write-Host $cmd
            if (-not $DryRun) {
                git branch -D $branch
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "Local delete of $branch failed; continuing."
                }
            }
        }
        else {
            Write-Host "  (local $branch not present, skipping)" -ForegroundColor DarkGray
        }
    }
}

# Remote deletes.
if (-not $SkipRemote) {
    Write-Host ""
    Write-Host "--- Origin branch deletes ---" -ForegroundColor Cyan
    foreach ($branch in $RetiredBranches) {
        $cmd = "git push origin --delete $branch"
        Write-Host $cmd
        if (-not $DryRun) {
            git push origin --delete $branch
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Origin delete of $branch failed (branch may already be gone)."
            }
        }
    }
}

# Prune local tracking refs that point at branches that no longer exist on origin.
if (-not $SkipRemote -and -not $DryRun) {
    Write-Host ""
    Write-Host "--- Pruning stale origin tracking refs ---" -ForegroundColor Cyan
    git remote prune origin
}

Write-Host ""
Write-Host "Sweep complete." -ForegroundColor Green
Write-Host "Retired branches: $($RetiredBranches.Count) (local + origin)" -ForegroundColor Green
Write-Host ""
Write-Host "Next: open the cleanup/v3-halt-2026-06-18 PR (the handoff docs and the rules update)." -ForegroundColor Cyan
