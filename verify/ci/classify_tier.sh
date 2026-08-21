#!/usr/bin/env bash
# Classify a PR as tier-3 or standard, from CI (2026-07-23, ADR-2026-07-23).
#
# Tier-3 used to be label-only ("tier-3" on the PR) with nothing applying the
# label -- so the deep gate never fired once in this repo's history. This
# script gives the label enforcement teeth: a PR is tier-3 if EITHER
#   (a) it carries the `tier-3` label (manual promotion, kept as an override), OR
#   (b) it touches any path matching verify/tier3_paths.txt (the sensitivity
#       list from .github/AUTO_MERGE.md, as regexes).
# There is deliberately NO label-based downgrade: removing the label from a
# PR that touches tier-3 paths does not demote it.
#
# Requires: gh (present on GitHub runners), GH_TOKEN, PR_NUMBER,
# GITHUB_REPOSITORY. Writes tier3 / tier_source / matched to GITHUB_OUTPUT.
set -euo pipefail

PATTERNS_FILE="${1:-verify/tier3_paths.txt}"
PR_NUMBER="${PR_NUMBER:?PR_NUMBER is required}"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
OUT="${GITHUB_OUTPUT:-/dev/stdout}"

if [ ! -f "$PATTERNS_FILE" ]; then
  echo "ERROR: tier-3 pattern file $PATTERNS_FILE not found" >&2
  exit 1
fi

tier3=false
source="none"
matched=""

# (a) manual label override
labels="$(gh api "repos/$REPO/pulls/$PR_NUMBER" --jq '[.labels[].name] | join(",")')"
case ",$labels," in
  *,tier-3,*) tier3=true; source="label" ;;
esac

# (b) changed paths vs the sensitivity list (via the API: no fetch-depth games)
files="$(gh api "repos/$REPO/pulls/$PR_NUMBER/files" --paginate --jq '.[].filename')"
patterns="$(grep -vE '^[[:space:]]*(#|$)' "$PATTERNS_FILE" || true)"
if [ -n "$patterns" ] && [ -n "$files" ]; then
  matched="$(printf '%s\n' "$files" | grep -E -f <(printf '%s\n' "$patterns") || true)"
fi
if [ -n "$matched" ]; then
  if [ "$tier3" = true ]; then source="label+paths"; else source="paths"; fi
  tier3=true
fi

{
  echo "tier3=$tier3"
  echo "tier_source=$source"
  echo "matched<<TIER3_EOF"
  printf '%s\n' "$matched"
  echo "TIER3_EOF"
} >> "$OUT"

echo "tier3=$tier3 (source: $source)"
if [ -n "$matched" ]; then
  echo "tier-3 paths touched:"
  printf '  %s\n' $matched
fi
