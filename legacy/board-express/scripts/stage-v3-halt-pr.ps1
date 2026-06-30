<#
.SYNOPSIS
    Stages the v3-halt cleanup PR: creates the branch and commits the new files.

.DESCRIPTION
    The 2026-06-18 v3-halt sweep produced four handoff docs, a docs/RULES.md update,
    and a delete-stalled-v-branches script. This script collects all of them into a
    single commit on a new branch (cleanup/v3-halt-2026-06-18) so Drew can open one PR.

    The sandbox that authored the files cannot do this step itself because it cannot
    remove a stale .git/index.lock on a Windows-mounted working tree (exactly the
    pattern the retro flagged). Run this from a clean Windows PowerShell session in
    C:\dev\agile-cards-board.

    This script does NOT push. Push is on Drew per SESSION_PROTOCOL.md section 7.

.NOTES
    Files committed:
      - docs/handoffs/HANDOFF_card-event-timeline_v3-halt_2026-06-18.md
      - docs/handoffs/HANDOFF_cmdk-filter-views_v3-halt_2026-06-18.md
      - docs/handoffs/HANDOFF_manual-rank_v3-halt_2026-06-18.md
      - docs/handoffs/HANDOFF_tile-polish_v3-halt_2026-06-18.md
      - docs/RULES.md
      - scripts/delete-stalled-v-branches.ps1
      - scripts/stage-v3-halt-pr.ps1 (this file)

    The repo's working tree on main has ~116 dirty files of suspected CRLF noise
    (per the retro). This script touches NONE of those. It checks out a new branch
    from main with no file changes, stages only the files listed above, commits.

.PARAMETER DryRun
    Print what would happen without executing.

.EXAMPLE
    .\scripts\stage-v3-halt-pr.ps1 -DryRun

.EXAMPLE
    .\scripts\stage-v3-halt-pr.ps1
#>

[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$BranchName = "cleanup/v3-halt-2026-06-18"
$CommitMessage = @"
chore(cleanup): v3-halt sweep, retire stalled -v branches

Per the 2026-06-18 third-party retro on C:\dev\, this repo had four
features sitting as v1+v2 or v1+v2+v3 rewrites, all stalled four weeks
ago, none merged. None of the higher-version branches represented a
redesign; they were recovery cherry-picks of identical implementations
on progressively newer bases after a stacked-PR auto-retarget trap on
2026-05-20 lied to GitHub about merge completeness.

This PR lands:

* Four HANDOFF docs (docs/handoffs/HANDOFF_<slug>_v3-halt_2026-06-18.md)
  documenting each feature's intent, what each version tried, where it
  stopped, which version is canonical, and what would need to happen to
  ship the canonical version. Each handoff carries an explicit "do NOT
  open a v4 without writing a paragraph first" line.

* docs/RULES.md formalizing the "v3 means halt" rule: opening any branch
  with a -v3 (or higher) suffix requires writing a halt-paragraph first.
  Branches without the paragraph are subject to deletion at the next
  sweep.

* scripts/delete-stalled-v-branches.ps1, a PowerShell script that
  deletes the six retired branches both locally and on origin. Run from
  a clean Windows PowerShell session, not from a sandbox.

* scripts/stage-v3-halt-pr.ps1, the script that produced this commit.

Canonical (kept):
  feature/card-event-timeline-v2
  feature/cmdk-filter-views-v3
  feature/manual-rank-v3
  feature/tile-polish-v2

Retired (deleted by the sweep script):
  feature/card-event-timeline
  feature/cmdk-filter-views
  feature/cmdk-filter-views-v2
  feature/manual-rank
  feature/manual-rank-v2
  feature/tile-polish

Refs: C:\dev\_retros\RETRO_2026-06-18.md move 3.
"@

$FilesToCommit = @(
    "docs/handoffs/HANDOFF_card-event-timeline_v3-halt_2026-06-18.md",
    "docs/handoffs/HANDOFF_cmdk-filter-views_v3-halt_2026-06-18.md",
    "docs/handoffs/HANDOFF_manual-rank_v3-halt_2026-06-18.md",
    "docs/handoffs/HANDOFF_tile-polish_v3-halt_2026-06-18.md",
    "docs/RULES.md",
    "scripts/delete-stalled-v-branches.ps1",
    "scripts/stage-v3-halt-pr.ps1"
)

Write-Host ""
Write-Host "v3-halt PR stager, 2026-06-18" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan

# Pre-flight.
$repoRoot = (git rev-parse --show-toplevel 2>$null)
if (-not $repoRoot) {
    Write-Error "Not inside a git repo. cd into C:\dev\agile-cards-board first."
    exit 1
}
if (-not ($repoRoot -replace '/','\').EndsWith("agile-cards-board")) {
    Write-Error "Wrong repo. Expected agile-cards-board, got '$repoRoot'."
    exit 1
}
Write-Host "Repo: $repoRoot"

# Remove any stale index.lock left behind by a sandbox attempt.
$lockPath = Join-Path $repoRoot ".git\index.lock"
if (Test-Path $lockPath) {
    Write-Host "Removing stale .git\index.lock" -ForegroundColor Yellow
    if (-not $DryRun) {
        Remove-Item -Force $lockPath
    }
}

# Confirm every file we plan to commit actually exists.
$missing = $FilesToCommit | Where-Object { -not (Test-Path (Join-Path $repoRoot $_)) }
if ($missing) {
    Write-Error "Missing files: $($missing -join ', ')"
    exit 1
}

# Make sure main is fetched fresh so the new branch starts from a current ref.
Write-Host ""
Write-Host "Fetching origin..." -ForegroundColor Cyan
if (-not $DryRun) {
    git fetch origin main
    if ($LASTEXITCODE -ne 0) { Write-Warning "git fetch failed; continuing with local main ref." }
}

# Create the cleanup branch from main without touching the dirty working tree.
Write-Host ""
Write-Host "Creating $BranchName from origin/main..." -ForegroundColor Cyan
if (-not $DryRun) {
    git checkout -B $BranchName origin/main --no-track
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Branch checkout failed. Resolve before retrying."
        exit 1
    }
}

# Stage only the v3-halt files. The 100+ dirty files on main are not part of this PR.
Write-Host ""
Write-Host "Staging v3-halt files only..." -ForegroundColor Cyan
foreach ($f in $FilesToCommit) {
    Write-Host "  + $f"
    if (-not $DryRun) {
        git add -- $f
        if ($LASTEXITCODE -ne 0) { Write-Warning "git add $f failed." }
    }
}

# Sanity: confirm staged set matches expected set.
if (-not $DryRun) {
    $staged = (git diff --cached --name-only) -split "`n" | Where-Object { $_ }
    $unexpected = $staged | Where-Object { $FilesToCommit -notcontains $_ }
    if ($unexpected) {
        Write-Error "Unexpected files in staging area: $($unexpected -join ', '). Aborting before commit."
        exit 1
    }
    if ($staged.Count -ne $FilesToCommit.Count) {
        Write-Warning "Staged $($staged.Count) files; expected $($FilesToCommit.Count)."
    }
}

# Commit.
Write-Host ""
Write-Host "Committing..." -ForegroundColor Cyan
if (-not $DryRun) {
    $tmp = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $tmp -Value $CommitMessage -Encoding UTF8
    git commit -F $tmp
    Remove-Item $tmp
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Commit failed."
        exit 1
    }
}

Write-Host ""
Write-Host "Done. Branch $BranchName has the v3-halt commit." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps for Drew:" -ForegroundColor Cyan
Write-Host "  1. git push -u origin $BranchName"
Write-Host "  2. gh pr create --base main --head $BranchName --title 'chore(cleanup): v3-halt sweep, retire stalled -v branches'"
Write-Host "  3. After PR merges, run .\scripts\delete-stalled-v-branches.ps1"
Write-Host ""
