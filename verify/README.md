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
  assertions/
    <surface>.yml        -- full assertion set per surface (deep verify)
    README.md            -- explains the assertions/ convention
  README.md              -- this file
```

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
