#!/usr/bin/env bash
# Syntax-check every inline `script:` body in a workflow file.
#
# WHY THIS EXISTS. verify-gate's verdict logic is JavaScript embedded in YAML and
# run by actions/github-script. A syntax error in it is invisible until a CI run
# fails, and because verify-gate is the REQUIRED context, that failure blocks the
# PR while looking like a gate failure rather than a typo.
#
# This is not hypothetical. paradigm-agilecards #74 wrote
#   quickReason = 'quick-verify booted this PR's backend artifact and smoked it';
# with a bare apostrophe inside a single-quoted JS string. github-script died with
# `SyntaxError: Unexpected identifier 's'`, verify-gate failed, and the PR was
# blocked by its own gate's typo. Escaping a quote through a YAML block scalar into
# JS is easy to get wrong, which is exactly why a machine should check it.
#
# WHAT IT DOES NOT DO. `node --check` parses; it does not run. It catches syntax
# errors, not logic errors, and nothing here knows whether the verdict is correct.
# It is a spell-checker for the gate, not a test of the gate. The gate's behaviour
# is covered by gate_selftest.sh.
#
# Each body is wrapped in `async function main(){ ... }` before checking, because
# github-script bodies legitimately use top-level `await` and `return`, which are
# syntax errors in a plain script but fine inside an async function. Checking them
# unwrapped would produce false failures.
#
# Requires: yq (mikefarah v4) and node, both already present in the verify jobs.
# Usage: bash verify/ci/gate_script_syntax.sh [.github/workflows/verify.yml]
set -uo pipefail

WF="${1:-.github/workflows/verify.yml}"

if [ ! -f "$WF" ]; then
  echo "ERROR: no workflow file at $WF" >&2
  exit 1
fi
for tool in yq node; do
  command -v "$tool" >/dev/null 2>&1 || { echo "ERROR: $tool is required" >&2; exit 1; }
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# How many inline script bodies are there? A workflow with none is not a pass:
# this script is wired into repos whose verify-gate IS github-script, so zero
# means either the wrong file was passed or the gate was rewritten, and either
# way silently succeeding would leave the check watching nothing.
count="$(yq -r '[.jobs[].steps[] | select(.with.script != null)] | length' "$WF" 2>/dev/null || echo 0)"
if [ -z "$count" ] || [ "$count" = "null" ]; then count=0; fi

echo "Checking $WF: $count inline script block(s)"
if [ "$count" -eq 0 ]; then
  echo "ERROR: no inline 'script:' body found in $WF." >&2
  echo "This check is only wired into repos whose verify-gate runs actions/github-script." >&2
  echo "Zero blocks means the wrong file was passed, or the gate no longer uses github-script" >&2
  echo "and this step should be removed deliberately rather than left passing over nothing." >&2
  exit 1
fi

fail=0
i=0
while [ "$i" -lt "$count" ]; do
  body="$WORK/script_$i.js"
  {
    echo "async function main() {"
    yq -r "[.jobs[].steps[] | select(.with.script != null) | .with.script][$i]" "$WF"
    echo "}"
  } > "$body"
  if node --check "$body" 2>"$WORK/err_$i"; then
    echo "  PASS  block $i parses ($(wc -l < "$body") lines wrapped)"
  else
    echo "  FAIL  block $i does not parse:"
    sed 's/^/        /' "$WORK/err_$i"
    fail=$((fail + 1))
  fi
  i=$((i + 1))
done

echo
if [ "$fail" -ne 0 ]; then
  echo "gate script syntax: $fail of $count block(s) failed to parse"
  exit 1
fi
echo "gate script syntax: all $count block(s) parse"
