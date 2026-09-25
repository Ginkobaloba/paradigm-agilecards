#!/usr/bin/env bash
# Offline tests for verify/ci/classify_tier.sh. This repo had no test for its
# classifier at all before this file, which is why the classifier was three
# versions behind the shared upstream without anything noticing.
#
# No network, no GitHub: a stub `gh` on PATH answers the three API calls the
# script makes (PR labels, PR changed files, and the BASE copy of the pattern
# file). Changed-file lists are literal text, one path per line, so paths
# containing regex metacharacters reach the script unsplit.
#
# WHY THESE CASES AND NOT OTHERS. A skipped required check SATISFIES GitHub
# branch protection, so the only failure that matters here is the classifier
# answering "standard" for a PR that should have been tier-3: that makes the
# required "Deep Verify (tier-3 PRs only)" check skip and report GREEN having
# verified nothing. Every case below is one route to that wrong answer.
#
# The script itself is ported verbatim from paradigm-skills'
# verify/templates/repo_verify_dir/ci/classify_tier.sh (PR #7). Keep it that
# way: if a case here needs the script changed, change the TEMPLATE and re-port,
# or this repo starts the drift that gave the fleet four divergent copies.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLASSIFY="$HERE/classify_tier.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bin"
cat > "$WORK/bin/gh" <<'STUB'
#!/usr/bin/env bash
path="$2"
case "$path" in
  */pulls/*/files) [ -n "${STUB_FILES:-}" ] && printf '%s\n' "$STUB_FILES" ;;
  */contents/*)
    if [ -n "${STUB_BASE_PATTERNS_FILE:-}" ]; then cat "$STUB_BASE_PATTERNS_FILE"; else exit 1; fi ;;
  */pulls/*) printf '%s\n' "${STUB_LABELS:-}" ;;
  *) echo "stub gh: unexpected call: $*" >&2; exit 2 ;;
esac
STUB
chmod +x "$WORK/bin/gh"
export PATH="$WORK/bin:$PATH"
export GITHUB_REPOSITORY="example/paradigm-agilecards" PR_NUMBER="7" GH_TOKEN="stub"

pass=0; failn=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then echo "PASS  $1"; pass=$((pass+1));
  else echo "FAIL  $1 (expected $2, got $3)"; failn=$((failn+1)); fi
}

run() { # labels files [patterns_file] [base_patterns_file] -> "tier3/source" or "error"
  local out="$WORK/out"; : > "$out"
  local pfile="${3:-$PAT}"
  ( export STUB_LABELS="$1" STUB_FILES="$2" GITHUB_OUTPUT="$out"
    if [ -n "${4:-}" ]; then export STUB_BASE_PATTERNS_FILE="$4" BASE_SHA="basesha"; fi
    bash "$CLASSIFY" "$pfile" >/dev/null 2>&1 ) || { echo "error"; return; }
  echo "$(sed -n 's/^tier3=//p' "$out")/$(sed -n 's/^tier_source=//p' "$out")"
}

# A stand-in for this repo's real list: two genuinely sensitive paths, plus the
# two the real list also carries that the gate must never self-promote on.
PAT="$WORK/paths.txt"
printf '%s\n' '# comment' '^backend/cards_api/' '^engine/lib/verifier/' '^verify/' '^\.github/workflows/' > "$PAT"

# ---- the ordinary answers ---------------------------------------------------
check "docs-only PR -> standard" "false/none" "$(run "" "docs/README.md")"
check "tier-3 path with NO label -> tier-3 by paths" "true/paths" "$(run "" "backend/cards_api/auth.py")"
check "label alone still promotes" "true/label" "$(run "bug,tier-3" "docs/README.md")"
check "look-alike labels do not promote" "false/none" "$(run "tier-30,not-tier-3" "docs/README.md")"
check "rename OUT of a tier-3 path still counts" "true/paths" \
  "$(run "" "$(printf '%s\n%s' 'backend/moved.py' 'backend/cards_api/auth.py')")"

# ---- fail closed: each of these would otherwise answer "standard" -----------
check "MISSING pattern file -> error" "error" "$(run "" "backend/cards_api/x.py" "$WORK/nope.txt")"
EMPTY="$WORK/empty.txt"; printf '%s\n' '# only comments' '' > "$EMPTY"
check "EMPTY pattern file -> error" "error" "$(run "" "backend/cards_api/x.py" "$EMPTY")"
BAD="$WORK/bad.txt"; printf '%s\n' '^backend/(cards' > "$BAD"
check "INVALID regex -> error" "error" "$(run "" "backend/cards_api/x.py" "$BAD")"

# ---- the base-branch union --------------------------------------------------
GUTTED="$WORK/gutted.txt"; printf '%s\n' '^docs/nothing/' > "$GUTTED"
check "PR deleting the pattern that covers it -> still tier-3" "true/paths" \
  "$(run "" "backend/cards_api/auth.py" "$GUTTED" "$PAT")"
ADDED="$WORK/added.txt"; printf '%s\n' '^src/brand-new/' > "$ADDED"
check "pattern ADDED by the PR applies to that PR" "true/paths" \
  "$(run "" "src/brand-new/x.ts" "$ADDED" "$PAT")"

# ---- NEVER_TIER3_BY_PATH, which matters MORE in this repo than most ---------
# Unlike iep-coach, THIS repo's real verify/tier3_paths.txt lists neither
# ^verify/ nor ^\.github/workflows/, so the exemption is currently inert here and
# these cases are defence rather than a live fix. They are kept because the
# exemption is what makes adding those patterns safe: with the base-branch union
# in force, listing them would otherwise classify every PR that edits this gate as
# tier-3, so changing the gate would need a deep-verify report OF the gate change,
# proven by the gate being changed. The synthetic list below includes them so the
# exemption is tested even though the real list does not.
check "verify/** is not promoted by path" "false/none" "$(run "" "verify/ci/classify_tier.sh")"
check "workflows/verify.yml is not promoted by path" "false/none" "$(run "" ".github/workflows/verify.yml")"
check "the label still promotes an exempt-path PR" "true/label" "$(run "tier-3" "verify/ci/classify_tier.sh")"
# NARROW, not a blanket hole: every other workflow is still promotable.
check "a NON-verify workflow is still tier-3 by path" "true/paths" "$(run "" ".github/workflows/ci.yml")"
# And an exempt path alongside a genuinely sensitive one still promotes.
check "exempt path + sensitive path -> still tier-3" "true/paths" \
  "$(run "" "$(printf '%s\n%s' 'verify/ci/classify_tier.sh' 'backend/cards_api/auth.py')")"

echo
echo "classify_tier test: $pass passed, $failn failed"
[ "$failn" -eq 0 ]
