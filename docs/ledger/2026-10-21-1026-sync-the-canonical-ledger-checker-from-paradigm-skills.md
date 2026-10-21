# 2026-10-21 10:26 CDT - sync the canonical ledger checker from paradigm-skills
- **Who:** scripts/sync-ledger.ps1 in paradigm-skills
- **Change:** scripts/ledger.mjs, scripts/ledger.test.mjs, scripts/ledger-since.test.mjs replaced with the canonical copies from paradigm-skills 4f2a1df. The other synced files already matched.
- **Why:** 17 repos carried 8 drifting variants of the checker (Drew decision D9, 2026-10-21). The canonical adds the empty-field check (cloudflare-config #48), the en-dash check, and a self-check: `check` now refuses to run if this file differs from its sha256 header.
- **State after:** this copy is byte-identical to paradigm-skills. Do not edit it here; change it in paradigm-skills, stamp, merge, and re-sync.
- **Refs:** paradigm-skills scripts/sync-ledger.ps1; source commit 4f2a1df
