#!/usr/bin/env bash
# Classify a PR as tier-3 or standard, from CI -- verify v2.
#
# In v1, tier-3 was label-only (`tier-3` on the PR) and nothing applied the
# label, so the deep gate could go its whole life without firing once. v2 gives
# the tier teeth: a PR is tier-3 if EITHER
#   (a) it carries the `tier-3` label (manual promotion, kept as an override), OR
#   (b) it touches any path matching verify/tier3_paths.txt (one extended regex
#       per line, the machine-readable half of verify/tier_map.yml).
# There is deliberately NO label-based downgrade: removing the label from a PR
# that touches tier-3 paths does not demote it.
#
# WHY THE FAIL-CLOSED DIRECTION IS THE WHOLE POINT. A skipped required check
# SATISFIES GitHub branch protection. So if this script wrongly answers
# "standard", the deep gate skips and reports GREEN having verified nothing.
# Every error path below therefore exits non-zero rather than answering
# "standard", and the calling workflow must treat a missing/empty output as
# tier-3 (the documented `needs.classify.outputs.tier3 != 'false'` form), not as
# a reason to skip.
#
# The path list is PER-REPO config. This script is generic; do not hardcode repo
# paths here. The one exception is NEVER_TIER3_BY_PATH below, which names the
# gate's OWN files and is the same in every repo using this template.
#
# Requires: gh (present on GitHub runners), GH_TOKEN, PR_NUMBER,
# GITHUB_REPOSITORY. Reads BASE_SHA when set. Writes tier3 / tier_source /
# matched to GITHUB_OUTPUT.
set -euo pipefail

PATTERNS_FILE="${1:-verify/tier3_paths.txt}"
PR_NUMBER="${PR_NUMBER:?PR_NUMBER is required}"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
OUT="${GITHUB_OUTPUT:-/dev/stdout}"

# The gate's own definition can NEVER be promoted to tier-3 by a PATH match.
#
# This is not an omission from the pattern file, it is a hard invariant, and the
# base-branch union below is what makes it necessary. A repo whose tier3_paths.txt
# contains `^\.github/workflows/` or `^verify/` would otherwise classify every PR
# that edits this gate as tier-3 by the BASE copy of the list, so changing the
# gate would require a deep-verify report OF THE GATE CHANGE ITSELF, proven by
# the gate being changed. That is question-begging, and in practice it is how a
# gate ends up switched off by someone who needs to land a CI fix.
#
# Deliberately NARROW: only `verify/**` and `.github/workflows/verify.yml`, which
# ARE this gate. Every other workflow is still promotable by path, because a bug
# in a deploy or release workflow can touch live credentials and is exactly the
# kind of change a repo may want gated. A manual `tier-3` label still promotes
# ANY pull request, including one that touches only these files: this exemption
# blocks automatic path promotion, never the human override.
NEVER_TIER3_BY_PATH='^verify/|^\.github/workflows/verify\.yml$'

# One extended regex per line; blank lines and whole-line # comments dropped. CR
# is stripped so a CRLF checkout cannot turn every pattern into one that matches
# only paths ending in a carriage return.
strip_patterns() { grep -vE '^[[:space:]]*(#|$)' | tr -d '\r' || true; }

head_patterns=""
if [ -f "$PATTERNS_FILE" ]; then
  head_patterns="$(strip_patterns < "$PATTERNS_FILE")"
fi

# The BASE branch's copy of the list, unioned with this PR's own.
#
# Without this, a PR can delete the pattern that would have classified it, in
# the same diff that touches the path that pattern covered, and classify itself
# standard. Read through the API so there are no fetch-depth games. A missing
# file on the base is normal (it is how the list gets introduced) and is not an
# error; only an empty UNION is.
base_patterns=""
if [ -n "${BASE_SHA:-}" ]; then
  # A 404 means there is no list on the base yet, which is how the list gets
  # introduced, so fall back to this PR's copy. ANY OTHER failure (500, a network
  # blip, an auth problem) must FAIL CLOSED: treating it as "no base patterns"
  # would silently drop the base's list, and the whole point of reading it is to
  # stop a PR deleting the pattern that covers it. This distinction came from
  # portal-shell, which had it in workflow YAML while this script did not.
  base_err="$(mktemp)"
  if base_raw="$(gh api "repos/$REPO/contents/$PATTERNS_FILE?ref=$BASE_SHA" \
      -H "Accept: application/vnd.github.raw" 2>"$base_err")"; then
    base_patterns="$(printf '%s\n' "$base_raw" | strip_patterns)"
  # This keys on gh's message TEXT, which is a real coupling. It is deliberate,
  # and the failure direction is what makes it acceptable: if gh ever reworded a
  # 404, this grep stops matching and the `else` branch runs, so a missing base
  # list would FAIL the job instead of being waved through. A wording change
  # makes the gate stricter, never looser. Do not "fix" this by loosening the
  # match to something like `grep -q 404`, which would match a 404 inside an
  # unrelated message and turn a real error into a silent fallback.
  elif grep -q 'HTTP 404' "$base_err"; then
    echo "note: no $PATTERNS_FILE on the base ($BASE_SHA); using this PR's list only." >&2
  else
    cat "$base_err" >&2
    rm -f "$base_err"
    echo "ERROR: could not read the base branch's $PATTERNS_FILE at $BASE_SHA." >&2
    echo "Failing closed: without the base list, a PR that deletes the pattern covering it" >&2
    echo "would classify as standard." >&2
    exit 1
  fi
  rm -f "$base_err"
fi

patterns="$(printf '%s\n%s\n' "$head_patterns" "$base_patterns" | grep -v '^$' | sort -u || true)"

# A file that exists but yields ZERO patterns is the quietest fail-open there is:
# nothing matches, every PR is "standard", and the gate reports green forever.
# Treated exactly like a missing file.
if [ -z "$patterns" ]; then
  echo "ERROR: no tier-3 patterns resolved. Failing closed." >&2
  echo "  head copy ($PATTERNS_FILE): $([ -f "$PATTERNS_FILE" ] && echo present || echo missing)" >&2
  echo "  base copy (ref ${BASE_SHA:-unset}): $([ -n "$base_patterns" ] && echo present || echo 'missing or empty')" >&2
  echo "An empty list classifies every PR as standard, which is the failure this gate exists to prevent." >&2
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
# A rename reports the NEW path as .filename; include .previous_filename so
# moving a file out of a tier-3 directory still counts as touching it.
files="$(gh api "repos/$REPO/pulls/$PR_NUMBER/files" --paginate --jq '.[] | .filename, (.previous_filename // empty)')"
exempted=""
if [ -n "$files" ]; then
  # grep exit 1 = no match (fine). Exit 2 = invalid regex, which must fail
  # closed instead of silently classifying every PR as standard.
  rc=0
  raw_matched="$(printf '%s\n' "$files" | grep -E -f <(printf '%s\n' "$patterns"))" || rc=$?
  if [ "$rc" -gt 1 ]; then
    echo "ERROR: invalid extended regex in the tier-3 pattern list (grep exit $rc). Failing closed." >&2
    exit 1
  fi
  if [ -n "$raw_matched" ]; then
    matched="$(printf '%s\n' "$raw_matched" | grep -vE "$NEVER_TIER3_BY_PATH" || true)"
    exempted="$(printf '%s\n' "$raw_matched" | grep -E "$NEVER_TIER3_BY_PATH" || true)"
  fi
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
echo "patterns in force: $(printf '%s\n' "$patterns" | wc -l | tr -d ' ') (union of base and head)"
if [ -n "$matched" ]; then
  echo "tier-3 paths touched:"
  printf '%s\n' "$matched" | sed 's/^/  /'
fi
if [ -n "$exempted" ]; then
  echo "matched but EXEMPT from path promotion (the gate's own files):"
  printf '%s\n' "$exempted" | sed 's/^/  /'
fi
