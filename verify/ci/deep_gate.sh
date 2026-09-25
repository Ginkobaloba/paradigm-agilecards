#!/usr/bin/env bash
# Deep-verify gate for tier-3 PRs -- v2 (2026-07-23, ADR-2026-07-23).
#
# Layers 5 and 6 (real-Chrome headed run and adversarial generation) cannot
# run in CI -- they need a Windows desktop with the computer-use MCP. So this
# gate does not fake a deep run. It requires committed evidence that a human
# ran one: a report under verify/reports/ that records a PASS.
#
# v2 change: the report must be PINNED TO THIS PR. v1 accepted any report
# anywhere in verify/reports/ with a PASS marker, so a single stale report
# would have satisfied the gate for every future tier-3 PR (same flaw found
# in portal-shell's copy). v2 requires a "Verified-Commit: <sha>" line whose
# sha (>= 12 hex chars) is one of this PR's commits. Committing the report is
# itself a commit, so the verified sha is normally the report commit's parent
# -- any commit in the PR is accepted. Commits pushed AFTER the report are the
# accepted residual risk; the audit note records head_sha so drift is visible.
#
# Requires: gh, GH_TOKEN, PR_NUMBER, GITHUB_REPOSITORY.
set -uo pipefail
shopt -s nullglob

PR_NUMBER="${PR_NUMBER:?PR_NUMBER is required}"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

# README/TEMPLATE files under reports/ are documentation, not evidence.
reports=()
for f in verify/reports/*.md; do
  case "$(basename "$f" | tr '[:upper:]' '[:lower:]')" in
    readme*|template*) continue ;;
    *) reports+=("$f") ;;
  esac
done

if [ ${#reports[@]} -eq 0 ]; then
  echo "Tier-3 PR: no deep-verify report found under verify/reports/."
  echo "Run /verify deep <target> locally, then commit the report (seed it from"
  echo "verify/REPORT_TEMPLATE.md; it must include 'Overall: PASS' and a"
  echo "'Verified-Commit: <sha>' line naming a commit in this PR) before merge."
  exit 1
fi

pr_shas="$(gh api "repos/$REPO/pulls/$PR_NUMBER/commits" --paginate --jq '.[].sha')"

satisfied=""
for r in "${reports[@]}"; do
  echo "== report: $r"
  if ! grep -qiE 'deep verify:[[:space:]]*pass|verdict:[[:space:]]*pass|overall:[[:space:]]*pass' "$r"; then
    echo "   no PASS marker (expect a line like 'Overall: PASS') -- ignored"
    continue
  fi
  sha="$(grep -iE '^[[:space:]]*verified-commit:' "$r" | head -1 \
        | sed -E 's/^[[:space:]]*[Vv]erified-[Cc]ommit:[[:space:]]*//' \
        | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  if [ -z "$sha" ]; then
    echo "   PASS marker but no 'Verified-Commit: <sha>' line -- ignored (v2 requires pinning)"
    continue
  fi
  if [ "${#sha}" -lt 12 ]; then
    echo "   Verified-Commit '$sha' shorter than 12 hex chars -- ignored"
    continue
  fi
  if printf '%s\n' "$pr_shas" | grep -q "^$sha"; then
    echo "   PASS + Verified-Commit $sha is a commit in PR #$PR_NUMBER -- gate satisfied"
    satisfied="$r"
    break
  fi
  echo "   Verified-Commit $sha is NOT a commit in PR #$PR_NUMBER -- stale or foreign report, ignored"
done

if [ -n "$satisfied" ]; then
  echo "Deep-verify gate satisfied by $satisfied."
  exit 0
fi

echo "Deep-verify gate NOT satisfied: no committed report both records a PASS"
echo "and pins a Verified-Commit belonging to this PR."
exit 1
