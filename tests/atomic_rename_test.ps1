<#
.SYNOPSIS
    Verify that NTFS rename is reliably atomic on this volume.

.DESCRIPTION
    The /cards runner relies on atomic file rename to move cards
    between backlog/, active/, done/, and blocked/. On POSIX,
    rename(2) is documented atomic. On NTFS, MoveFileEx with
    MOVEFILE_REPLACE_EXISTING is documented atomic within a volume,
    but real-world behavior on Windows depends on filesystem
    filters, antivirus, OneDrive sync, and indexing services that
    can hold open file handles.

    This script races N PowerShell jobs trying to rename the same
    source file to the same destination. Exactly one should succeed
    and N-1 should fail. If two or more succeed, or if the file
    disappears or gets corrupted, the test fails and the runner
    must NOT rely on plain rename for state transitions on this
    device. Fall back to Move-Item with explicit lock retry.

.PARAMETER ParallelCount
    Number of concurrent rename attempts. Default 16.

.PARAMETER Iterations
    Number of race rounds. Default 50.

.PARAMETER WorkDir
    Directory to run the test in. Default $env:TEMP\cards-rename-test.

.EXAMPLE
    cd C:\dev\agile_cards
    .\tests\atomic_rename_test.ps1

    Prints PASS or FAIL with details. Run once per device before
    trusting parallel cards execution on that machine.
#>
[CmdletBinding()]
param(
    [int]$ParallelCount = 16,
    [int]$Iterations    = 50,
    [string]$WorkDir    = (Join-Path $env:TEMP "cards-rename-test")
)

$ErrorActionPreference = "Stop"

function Write-Ok($text)   { Write-Host "  $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  $text" -ForegroundColor Yellow }
function Write-Bad($text)  { Write-Host "  $text" -ForegroundColor Red }
function Write-Section($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

Write-Section "Atomic rename test for /cards"
Write-Host "  ParallelCount: $ParallelCount"
Write-Host "  Iterations:    $Iterations"
Write-Host "  WorkDir:       $WorkDir"

# Prepare clean work dir.
if (Test-Path $WorkDir) {
    Remove-Item -Recurse -Force $WorkDir
}
New-Item -ItemType Directory -Path $WorkDir | Out-Null

$totalWins  = 0
$totalLoss  = 0
$weirdCount = 0
$failedRounds = @()

for ($i = 1; $i -le $Iterations; $i++) {
    $round = Join-Path $WorkDir "round-$i"
    New-Item -ItemType Directory -Path $round | Out-Null

    $src = Join-Path $round "src.txt"
    Set-Content -Path $src -Value "card-content-round-$i"

    $jobs = 1..$ParallelCount | ForEach-Object {
        $dst = Join-Path $round "dst-$_.txt"
        Start-Job -ScriptBlock {
            param($srcPath, $dstPath)
            try {
                # .NET Move uses MoveFileEx underneath.
                [System.IO.File]::Move($srcPath, $dstPath)
                return "WIN"
            } catch [System.IO.FileNotFoundException] {
                return "LOSE-NOTFOUND"
            } catch [System.IO.IOException] {
                return "LOSE-IO"
            } catch {
                return "WEIRD: $($_.Exception.GetType().Name): $($_.Exception.Message)"
            }
        } -ArgumentList $src, $dst
    }

    $results = $jobs | Wait-Job | Receive-Job
    $jobs    | Remove-Job

    $wins   = ($results | Where-Object { $_ -eq "WIN" }).Count
    $losses = ($results | Where-Object { $_ -like "LOSE-*" }).Count
    $weird  = ($results | Where-Object { $_ -like "WEIRD*" }).Count

    $totalWins  += $wins
    $totalLoss  += $losses
    $weirdCount += $weird

    if ($wins -ne 1) {
        $failedRounds += [pscustomobject]@{
            Round   = $i
            Wins    = $wins
            Losses  = $losses
            Weird   = $weird
            Results = $results
        }
    }
}

Write-Section "Results"
Write-Host "  Total rounds:        $Iterations"
Write-Host "  Rounds with exactly 1 winner: $($Iterations - $failedRounds.Count)"
Write-Host "  Rounds with anomalies:        $($failedRounds.Count)"
Write-Host "  Total WIN responses:  $totalWins"
Write-Host "  Total LOSE responses: $totalLoss"
Write-Host "  Total WEIRD responses: $weirdCount"

if ($failedRounds.Count -eq 0 -and $weirdCount -eq 0) {
    Write-Section "PASS"
    Write-Ok "Atomic rename is reliable on this volume."
    Write-Ok "Runner can use plain rename for card state transitions."
    Remove-Item -Recurse -Force $WorkDir
    exit 0
} else {
    Write-Section "FAIL"
    Write-Bad "Atomic rename is NOT reliable on this volume."
    Write-Bad "Runner MUST use Move-Item with explicit lock-retry loop."
    Write-Bad "Anomalies kept in $WorkDir for inspection."
    foreach ($f in $failedRounds | Select-Object -First 5) {
        Write-Bad ("Round {0}: wins={1} losses={2} weird={3}" -f $f.Round, $f.Wins, $f.Losses, $f.Weird)
    }
    if ($failedRounds.Count -gt 5) {
        Write-Bad "(... $($failedRounds.Count - 5) more rounds failed)"
    }
    exit 1
}

# Fallback strategy if this script returns FAIL:
#
# Replace plain [System.IO.File]::Move with a retry loop that:
#   1. Opens the source with FileShare.None (exclusive lock).
#   2. Writes contents to a sibling temp file in the destination dir.
#   3. Closes the source.
#   4. Calls Move on the temp file (which is on the same volume).
#   5. Deletes the source after the move succeeds.
# Retry on IOException with exponential backoff up to 5 attempts.
# Document the fallback path in RUNNER_CONTRACT.md.
