# Handoff: gate chunk 3 -- calibration loop + ramp CLI (2026-06-09)

Session: Fable run dispatched from Dispatch. CTO-mode velocity rules in
effect (Tier 1/2 agent-reviewed and auto-merged; Tier 3 to Drew).

## What this session did

1. **Synced state.** Local repos were stale; fetched + pruned. Confirmed
   `delete_branch_on_merge` true on both agile-cards and
   agile-cards-board. Deleted 12 local branches with gone upstreams
   (all content verified merged, including the S41 DEFINITION_OF_DONE
   branch whose PR landed).
2. **Built and merged gate chunk 3** (PR #34, squash-merged after agent
   review + green CI). Per the sprint plan
   (`outputs/SPRINT_agile-cards_2026-06-01.md` section 5) and the
   design (`docs/design/confidence_driven_merge_gate.md` sections
   7.3 / 8 / 9):
   - `metrics/calibration.py`: bands gate-2b shadow decisions by
     confidence decile, joins each card's latest decision against its
     regression outcome, reports per-band rates + monotonicity.
   - `metrics/ramp.py`: per-bucket phase 1-4 state, spec 9.3
     advancement gates with named evidence checks, operator-explicit
     +1 advancement.
   - New `gate_ramp` table (documented deviation from spec 9.5: a
     phase column on `metric_estimates` would reset on every
     recalibration because that cache refreshes via INSERT OR
     REPLACE; a pinning test proves the survival property).
   - CLI: `cards-runner stats calibration` and
     `cards-runner stats ramp show|advance --bucket wt:tier --confirm`.
   - Event kinds for phase advance/recommendation (emitted now) and
     live-decision / kill-switch (vocabulary defined, chunk 4 emits).
3. **Agent review caught two blocking bugs before merge** (both event
   readers were duplicate-blind in the permissive direction; a
   replayed `cleared` could absorb the next real kill-switch trip).
   Fixed with last-event-wins / distinct-dedup-key reads plus a
   banding-epsilon fix for boundary-exact scores (0.95 at 20 bands is
   the auto threshold). 670 tests pass, 40 new this session.

## State of the gate stack (design section 13)

| chunk | status |
|---|---|
| gate-1 risk-factor schema | merged (#26) |
| gate-2 decision engine + shadow wiring | merged (#32, #33) |
| gate-3 calibration + ramp CLI | **merged this session (#34)** |
| gate-4 live-mode wiring + kill-switch | NOT built. **Tier 3** -- flipping routing authority to the confidence signal is Drew's call, and spec 9.3 wants n>=30 shadow decisions per active bucket with monotonic calibration first. Shadow data accrues only when the daemon runs cards. |
| gate-5 expected_files planner field | not built, additive, Tier 1 |
| gate-6 fitted model | needs n=300; far out |

## What is currently broken or incomplete

- Nothing failing. 670/670 green on main.
- Shadow-decision volume is the bottleneck for everything downstream:
  the calibration table is empty until the daemon runs real cards with
  the ledger enabled. The CLI handles the empty state cleanly.
- The metrics SQL surface (MetricsStore, RampStore) remains de facto
  SQLite-only; DoltRepository's raw pymysql connection would not
  satisfy the `_Connection` protocol. Pre-existing condition, noted by
  the review agent, not introduced here. Worth a card if Dolt becomes
  real.

## What the next session should do first

1. Read this handoff, the sprint plan, and the gate design doc.
2. If continuing agile-cards: **gate-5** (`expected_files:` planner
   field + scope soft signal) is the remaining Tier-1 additive chunk.
   Ledger chunks 5 (contract-survival read API) and 6 (trust signal)
   from the ledger spec are also open, Tier 2.
3. **gate-4 is held for Drew** per the sprint plan section 8.1. Do not
   build live-mode routing without his explicit go.
4. To start accruing shadow data: run the daemon with the ledger on
   against a real card batch; then `cards-runner stats calibration`
   and `stats ramp show` become meaningful.

## Open questions for Drew

- Gate-4 (live mode) go/no-go remains the standing Tier-3 decision.
  Nothing this session changed its readiness: shadow n is still ~0.
- The spec 12 open questions (12.2 skipped-verifier, 12.3 haiku-only,
  12.4 expected_files) still carry their recommended defaults,
  unratified. They bind at gate-4 only.

## Pointers

- Design: `docs/design/confidence_driven_merge_gate.md`
- Sprint plan (living draft): `outputs/SPRINT_agile-cards_2026-06-01.md`
- Sibling repo session: agile-cards-board (saved views v0 confirmed
  merged as #37 there; triage inbox is its next chunk -- see that
  repo's handoff of the same date).

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then this
project's `README.md` / `RUNNER_CONTRACT.md` / `SKILL.md`, then this
file, then run `vstart`. Note: `session-start.ps1` trips PowerShell
5.1's NativeCommandError on `git fetch 2>&1` (the fetch succeeds; the
script exits 1). Run the protocol steps manually if it fails.
