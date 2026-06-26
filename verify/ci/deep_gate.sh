#!/usr/bin/env bash
# Deep-verify gate for tier-3 PRs. Layers 5 and 6 (real-Chrome headed run and
# adversarial generation) cannot run in CI -- they need a Windows desktop with the
# computer-use MCP. So this gate does not fake a deep run. It requires committed
# evidence that a human ran one: a report under verify/reports/ that records a
# PASS. No report, or a non-PASS report, means the gate is not satisfied and the
# tier-3 PR must not merge.
set -uo pipefail
shopt -s nullglob

reports=(verify/reports/*.md)
if [ ${#reports[@]} -eq 0 ]; then
  echo "Tier-3 PR: no deep-verify report found under verify/reports/."
  echo "Run /verify deep <target> locally, then commit the report to verify/reports/ before merge."
  exit 1
fi

echo "Found deep-verify report(s): ${reports[*]}"
if grep -liE 'deep verify:[[:space:]]*pass|verdict:[[:space:]]*pass|overall:[[:space:]]*pass' "${reports[@]}" >/dev/null; then
  echo "Deep-verify report shows PASS. Gate satisfied."
  exit 0
fi
echo "Deep-verify report present but no PASS marker found (expect a line like 'Overall: PASS')."
echo "Gate not satisfied."
exit 1
