# verify/

This directory declares what "passing" means for **this repo**. The engine that
reads and executes these files is the `paradigm-verify` skill. No central
choke point -- each repo owns its own assertions.

## Why this exists

Central verify configs become coupling points. A per-repo `verify/` lets each
project define its own surfaces, thresholds, and risk tiers without touching
shared infrastructure. The skill is the engine; this directory is the spec.

## File layout

```
verify/
  smoke.yml              -- fast surface checks run on every PR
  tier_map.yml           -- surface risk classification (Tier 1-3)
  tier3_paths.txt        -- machine-readable tier-3 path regexes (classify job)
  REPORT_TEMPLATE.md     -- seed for committed deep-verify reports
  reports/               -- committed deep-verify evidence (tier-3 gate input)
  ci/
    quick_smoke.sh       -- live smoke runner (quick-verify job)
    classify_tier.sh     -- tier-3 classification (label OR tier3_paths.txt)
    deep_gate.sh         -- tier-3 gate: PASS report pinned to a PR commit
    apply_branch_protection.ps1 -- converge the main ruleset (ADR-2026-07-23)
  assertions/
    <surface>.yml        -- full assertion set per surface (deep verify)
    README.md            -- explains the assertions/ convention
  README.md              -- this file
```

## CI shape (ADR-2026-07-23)

`verify-gate` is the single required status check from this suite. It always
reports, aggregating detect/classify/quick/deep results, and writes a
persistent audit note per run (pinned issue "Verify Audit Log
(paradigm.verify-audit/v1)" + sticky PR comment). quick-verify skips honestly
while `smoke.yml` `deploy_url` is a placeholder; deep-verify fires on tier-3
PRs (path-based via `tier3_paths.txt`, or the `tier-3` label) and demands a
committed report whose `Verified-Commit` is a commit of the PR.

## How to run

**Quick (every PR):**
```
/verify <repo-path>
```
Runs `smoke.yml` only. Fast. Safe to require in CI.

**Deep (before merge on Tier-3 surfaces):**
```
/verify deep <repo-path>
```
Runs all `assertions/<surface>.yml` files. Required before merging any PR
that touches a surface marked `tier: 3` in `tier_map.yml`.

## How to extend

- **Add a surface to smoke.yml** -- append a new entry under `surfaces:` with
  at least an `http_status` assertion.
- **Add full assertions** -- create `assertions/<surface>.yml` mirroring the
  surface name from `smoke.yml`. Use the `assertions/home.yml` file as the
  template.
- **Mark a surface Tier-3** -- add or update the entry in `tier_map.yml` with
  `tier: 3` and `deep_verify_before_merge: true`. Include a `reason`.

## Placeholders

All `<ALL_CAPS>` values in these files are project-specific and must be
replaced before the verify suite is meaningful. Search for `<` to find them.
