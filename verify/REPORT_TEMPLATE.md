# Deep-verify report -- <target> -- <YYYY-MM-DD>

<!--
Seed for reports committed to verify/reports/ (name them
DEEP_<YYYY-MM-DD>_<short>.md). The CI gate (verify/ci/deep_gate.sh) requires,
for a tier-3 PR to merge:
  1. a line 'Overall: PASS' (or 'Verdict: PASS' / 'Deep Verify: PASS'), and
  2. a line 'Verified-Commit: <sha>' where <sha> (>= 12 hex chars) is a commit
     in the PR under review. Use `git rev-parse HEAD` in your branch BEFORE
     committing the report -- the report commit's parent is what you verified.
Reports are permanent audit evidence: do not edit them after merge; write a
new one instead.
-->

Verified-Commit: <full sha of the commit you actually verified>
Branch: <branch name>
PR: #<number>
Operator: <who ran it>
Environment: <e.g. Windows 11 desktop, Chrome + computer-use MCP, local board at http://localhost:...>

## Layers run

| layer | scope | result |
|---|---|---|
| 1-4 (network/headless) | <what> | PASS/FAIL |
| 5 (headed, computer-use) | <surfaces exercised, e.g. api-card-move round-trip> | PASS/FAIL |
| 6 (adversarial) | <generation scope> | PASS/FAIL |

## Evidence

- <what was clicked/asserted, files/screenshots if any, anomalies observed>

## Notes / follow-ups

- <anything found that did not block the PASS>

Overall: <PASS|FAIL>
