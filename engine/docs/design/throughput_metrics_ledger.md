# Throughput-Metrics Ledger

Status: DESIGN. Not implemented. Awaiting Drew's approval before any code or
schema changes land.

Author: planner pass (Claude, Opus 4.7)
Created: 2026-05-23
Branch (suggested): `feature/throughput-metrics-ledger`
Depends on: RUNNER_CONTRACT.md (the card lifecycle this measures), the chunk
5 `reviewer_history.jsonl` pattern (the audit-log shape this generalizes).
Independent of: the chunk 6 stack (this spec stands alone; it does not need
the auto_edit_ac, reviewer-cost-attribution, doctor, or signals-cleanup
PRs to be merged first).

---

## 1. Purpose

The runner already measures one thing well: agent-time. It tells us how many
tokens a card spent, which model produced them, and how long the executor
ran. None of those numbers are quotable. A Paradigm Coding Solutions client
does not want to know that an LLM churned for 47 minutes on their card;
they want to know **when their merged, reviewed, working code will land**.

That gap -- between agent-churn and quotable delivery -- is what this
ledger closes.

The ledger serves three discrete purposes. Each one is named here so a
design decision in section 3+ can be checked back against the purpose it
serves.

### 1.1 Realistic job quoting for Paradigm Coding Solutions

The quotable number to a client is:

```
quotable_time = build_time + review_time + rework_time
```

Never raw agent-churn time. A card whose executor spent 6 hours of wall
clock but whose human reviewer turned it around in 15 minutes is a fast
card; a card whose executor spent 30 minutes but whose reviewer needed two
days plus a rework cycle is a slow card. The ledger lets us quote with
honesty about all three components.

### 1.2 Self-calibrating roadmap estimates

The same numbers feed a different consumer: when we plan a sprint or batch
of cards internally, the ledger predicts each card's effort with explicit
variance bands. The model recalibrates as cards complete -- early
estimates are derived from priors, later estimates are derived from
observed empirical performance on this work-type + tier.

### 1.3 Trust signal for auto-merged work

The chunk 4 merge gate auto-merges tier 1-2 cards. The chunk 5 sibling
reviewer can auto-approve tier 3-4. Both are speed wins **only if the
auto-merged work stays good**. The ledger tracks a rolling "did this
auto-merge cause a follow-up bugfix?" rate and surfaces it as a knob the
operator uses to decide whether to relax or tighten the merge gate.

---

## 2. Scope and non-goals

### In scope

- A schema that records one normalized metrics row per card, populated at
  card lifecycle events (creation, claim, executor exit, verifier
  decision, PR open, review decision, merge, rework trigger).
- An append-only event log capturing every metric-relevant transition --
  so a quote derived from the ledger is reproducible from the audit trail.
- A work-type taxonomy enforced at card creation.
- A Bayesian-with-shrinkage empirical estimator for per-`(work_type,
  tier)` effort with variance bands.
- A contract-survival metric for measuring whether speculative parallelism
  is paying off.
- A read API (Python + CLI) that surfaces quotes, roadmap estimates, and
  the auto-merge trust signal.

### Explicit non-goals (deferred or out of scope)

- **No new dashboards.** A CLI `cards-runner stats` subcommand is in
  scope; an HTML dashboard is not (dashboards belong to a future chunk
  layered on top of the read API).
- **No automatic merge-gate adjustment.** The trust signal is *surfaced*;
  the operator chooses whether to flip a knob in response. An autonomous
  closed-loop trust-driven gate change is risky and out of scope here.
- **No cross-project rollup yet.** The ledger is per-`todo_root` /
  per-project. Aggregating across projects requires shared
  identifier-space work that is not in this design.
- **No retroactive backfill for pre-ledger cards.** Existing cards get a
  best-effort `work_type` stamp (default `feature` or `unknown`) and an
  `incomplete_metrics=True` flag. The estimator excludes incomplete-metric
  rows from its training set.
- **No real-time streaming.** The ledger is queried on demand; the
  runner is not expected to produce a live feed.

---

## 3. The ledger data model

### 3.1 Per-card metrics row

One row per card, in a new `card_metrics` table:

| column | type | source | nullable | purpose |
|---|---|---|---|---|
| `card_id` | TEXT (PK) | runner | no | foreign key to `cards.card_id` |
| `tenant_id` | TEXT (PK) | runner | no | tenant scope |
| `work_type` | TEXT | planner | no | one of section 4's taxonomy |
| `tier` | INTEGER | planner | no | mirror of `cards.points` |
| `pin_required` | BOOLEAN | planner | no | mirror of `cards.pin_required` |
| `contract_authored_at` | TEXT (ISO) | planner | yes | when AC was first written |
| `started_at` | TEXT (ISO) | runner | yes | mirror of `cards.started_at` |
| `finished_at` | TEXT (ISO) | runner | yes | mirror of `cards.finished_at` |
| `agent_wall_seconds` | REAL | runner | yes | `finished_at - started_at` per attempt |
| `agent_attempts` | INTEGER | runner | yes | how many claims it took to land done/blocked |
| `executor_tokens_total` | INTEGER | runner | yes | sum across attempts |
| `executor_cost_usd` | REAL | runner | yes | derived from tokens via tier_pricing |
| `verifier_tokens_total` | INTEGER | runner | yes | LLM verification spend |
| `reviewer_tokens_total` | INTEGER | runner | yes | sibling + amendment reviewer spend |
| `human_review_wall_seconds` | REAL | gh poller | yes | PR open -> human decision |
| `rework_cycles` | INTEGER | runner | yes | count of verifier FAIL + change_request loops |
| `diff_lines_added` | INTEGER | gh | yes | from PR stats at merge time |
| `diff_lines_removed` | INTEGER | gh | yes | same |
| `merge_gate` | TEXT | runner | yes | `auto` / `sibling_review` / `human_review` |
| `merged_at` | TEXT (ISO) | gh | yes | when the PR actually merged |
| `regression_card_ids` | JSON | runner | yes | follow-up bugfix card IDs flagged against this |
| `contract_survived` | BOOLEAN | runner | yes | see section 6 |
| `incomplete_metrics` | BOOLEAN | runner | no, default false | true when any field was best-effort filled |

**Design decision: separate `card_metrics` table, not new columns on
`cards`.** The chunk 2b `cards` table is the canonical state; adding ~20
derived numeric columns bloats the hot read path (every poll iteration
scans the cards table). `card_metrics` is read on demand by the
estimator and the read API; the daemon doesn't touch it on the polling
hot path. The PK joins one-to-one with `cards`.

### 3.2 Append-only event log

A new JSONL file `signals/metrics_events.jsonl` records every transition
that mutates a metrics field. Shape mirrors the chunk 6d
`reviewer_history.jsonl`:

```json
{
  "at": "2026-05-23T14:32:00Z",
  "card_id": "b042-03-add-rate-limit",
  "kind": "rework_triggered",
  "trigger": "verifier_fail",
  "rework_cycles_after": 2,
  "extra": {"failed_ac_index": 1}
}
```

`kind` values: `card_created`, `card_started`, `executor_exited`,
`verifier_decided`, `pr_opened`, `pr_reviewed`, `pr_merged`,
`rework_triggered`, `regression_flagged`, `contract_violation_detected`.

**Design decision: JSONL not a SQL table.** The events are append-only
and never queried in the hot path. JSONL is one `jq` away from any custom
analysis, survives schema migrations trivially, and matches the
established chunk 6d pattern. If a future consumer needs SQL access, a
periodic sync into a `metrics_events` table is mechanical.

### 3.3 Empirical estimate cache

A third table, `metric_estimates`, caches the latest recalibrated estimate
for each `(work_type, tier)` pair:

| column | type | purpose |
|---|---|---|
| `work_type` | TEXT (PK) | section 4 taxonomy |
| `tier` | INTEGER (PK) | 1-6 |
| `n_samples` | INTEGER | how many completed cards informed this estimate |
| `agent_wall_seconds_p50` | REAL | median |
| `agent_wall_seconds_p75` | REAL | 75th percentile |
| `agent_wall_seconds_p90` | REAL | 90th percentile |
| `executor_tokens_p50` | INTEGER | same percentile shape |
| `executor_tokens_p90` | INTEGER | |
| `human_review_wall_seconds_p50` | REAL | |
| `human_review_wall_seconds_p90` | REAL | |
| `rework_rate_mean` | REAL | E[rework_cycles] |
| `contract_survival_rate` | REAL | fraction over recent window |
| `last_calibrated_at` | TEXT (ISO) | when this row was last refreshed |
| `prior_weight` | REAL | how much shrinkage applied (0.0 = pure empirical) |

This is a derived view; on a fresh boot it can be reconstructed from
`card_metrics` alone. The cache exists so the read API answers in O(1)
per `(work_type, tier)`. Recalibration is cheap (small data; a SQL
window function or a 10-line Python loop) so we re-run it on a schedule.

---

## 4. Work-type taxonomy

Every card carries a `work_type:` frontmatter field, enforced by the
planner at card creation. Adding the field to the card schema is a
non-breaking change: existing cards without `work_type:` are stamped
`unknown` on load, with `incomplete_metrics=True`.

Canonical types:

| `work_type` | Definition |
|---|---|
| `feature` | New user-visible functionality. Default for ambiguous user-facing cards. |
| `refactor` | Internal restructure with no observable behavior change. |
| `bugfix` | Repairs a regression or defect. Must reference the broken behavior. |
| `infrastructure` | Tooling, CI, build, deploy, observability plumbing. |
| `docs` | Documentation-only. No code change beyond docstrings. |
| `spike` | Exploratory. Outcome is knowledge; may not produce a merge. |
| `contract` | Design-only. Writes a spec; does not implement. |
| `migration` | Schema or data migration. Includes runner schema bumps. |
| `unknown` | Reserved for pre-ledger backfill. New cards MUST NOT use this. |

**Design decision: nine categories, one of which is escape valve only for
backfill.** Larger taxonomies (20+ categories) fragment data; smaller
taxonomies (3-4) lose signal. Nine is empirically the band where each
type has enough cards in a year to support a useful empirical estimate
on a busy single-developer project.

**Design decision: `contract` is a first-class work_type.** A
contract-first design card writes a spec and stops. Without a distinct
`work_type`, contract cards skew the `feature` estimates downward (they
have low rework but zero implementation). Section 6's contract-survival
metric *also* uses `work_type=contract` as the input side -- it
measures what happens when those contracts are later implemented.

### 4.1 The work_type field on cards

Card frontmatter gains one required field:

```yaml
work_type: feature           # required, enum from the table above
```

The planner stamps it at card creation. The runner validates it on
projection (rejecting an unknown enum value with a clear error message);
chunk 6c's `doctor` reports validation failures.

### 4.2 Subtyping is intentionally out of scope

A first instinct is to attach a `work_subtype:` field (`feature.api`,
`feature.ui`, `refactor.tests-only`, ...). Resist. Subtypes fragment data
faster than they explain it. If a real subtype-driven need emerges later
(e.g. test-only cards review 3x faster than implementation cards), it
arrives as a *new top-level* work_type rather than a sub-category. This
is the same discipline that keeps the AC type registry small.

---

## 5. Storage and write surface

### 5.1 Schema migrations

Three migrations land in one chunk:

1. `CREATE TABLE card_metrics` per section 3.1.
2. `CREATE TABLE metric_estimates` per section 3.3.
3. `cards.work_type` becomes a new promoted column on the existing
   `cards` table (chunk 5's `pr_url` promotion is the precedent).
   `ADDED_COLUMNS` gains one entry.

The chunk 6c `doctor` subcommand reports applied vs pending for each.

### 5.2 Where each field gets written

**Author = the runner module that owns the write.**

| Field | Author | Trigger |
|---|---|---|
| `card_id`, `tenant_id`, `work_type`, `tier`, `pin_required` | planner | card creation (via `card_text_to_record` + projection) |
| `contract_authored_at` | planner | written when AC block is first authored; populated by `/cards` skill |
| `started_at`, `finished_at`, `agent_wall_seconds`, `agent_attempts` | daemon `_post_worker_exit` | every worker exit; cumulative across attempts |
| `executor_tokens_total`, `executor_cost_usd` | daemon `_post_worker_exit` | sums sidecar usage |
| `verifier_tokens_total` | daemon `_dispatch_verifier` | mirrors verifier's `cascade_history` |
| `reviewer_tokens_total` | sibling_reviewer + amendment_reviewer | chunk 6b attribution path, summed |
| `rework_cycles` | daemon | incremented on every verifier FAIL and every approved `change_request:` |
| `merge_gate` | merge_gate.MergeGate | written when the gate decides |
| `diff_lines_added`/`removed`, `merged_at` | unblocker | read from `gh pr view --json additions,deletions,mergedAt` |
| `human_review_wall_seconds` | unblocker | `merged_at - pr_opened_at` for human/sibling gates |
| `regression_card_ids` | bugfix-card planner | a new bugfix card stamps a `regresses:` field; the regression's metrics row appends to the parent's `regression_card_ids` |
| `contract_survived` | daemon `_route_approve_edited` (chunk 6a path) | False when any change_request approved during impl; True if card reaches `done` without amendment |
| `incomplete_metrics` | best-effort fillers | True when any field had to be defaulted |

All writes are guarded by the same best-effort-with-log convention chunk
6b's `attribute_to_card` uses: a write failure logs at WARNING but does
not abort the calling sweep. The audit log (5.3) is the authoritative
record; the row is a denormalization.

### 5.3 The audit log

Every write to `card_metrics` is preceded by an append to
`signals/metrics_events.jsonl`. The row can always be rebuilt from the
log; in practice the row is what the read API consults, but the log is
what's trusted in a "did we just lose data?" investigation.

### 5.4 Idempotency

Re-running a write (the daemon re-processes a worker exit after a
crash) MUST be safe:

- `card_metrics` writes are `INSERT OR REPLACE` keyed on
  `(card_id, tenant_id)`.
- The audit log is append-only; a duplicate event is tolerable and
  detectable by the rebuilder.
- Cumulative fields (`rework_cycles`, `agent_attempts`,
  `executor_tokens_total`) are computed from the audit log, not from
  the previous row value. This avoids the chunk 6b stale-read bug
  (reviewer + editor trampling each other) from the start.

---

## 6. Contract-survival metric

### 6.1 Definition

A card is **contract-first** if `contract_authored_at` is set AND
precedes `started_at` by at least `cfg.contract_lead_seconds` (default
600 -- ten minutes; below this we assume the contract was written
contemporaneously with implementation, which doesn't tell us anything
about speculative parallelism).

A contract-first card **survives unchanged** if it reaches a terminal
state (`done` or `blocked/merged`) with `rework_cycles=0` AND no
`change_request:` block was approved during implementation. Equivalently:
the AC the planner wrote up front is exactly the AC the implementation
satisfied.

**Contract-survival rate**, sliced by `(work_type, tier)` and over a
configurable window (default last 30 cards or 30 days, whichever is
larger):

```
contract_survival_rate(wt, tier) =
    count(card_metrics where contract_survived=True
        AND work_type=wt AND tier=tier
        AND finished_at >= window_start)
  / count(card_metrics where contract_authored_at is not null
        AND work_type=wt AND tier=tier
        AND finished_at >= window_start)
```

### 6.2 Why this matters

When the workflow is *contract-first, build speculatively in parallel*,
a single planner authors AC for N cards up front, then N executors run
those cards in parallel without coordination. The model breaks down when
contracts drift -- a card's implementation forces an AC amendment that
invalidates a sibling card's assumptions.

A high contract-survival rate (say > 0.8) means the planner is sizing
contracts to fit reality. A low rate (say < 0.5) means one of:

- The planner is authoring contracts too aggressively, without sufficient
  domain understanding to predict the implementation. Slow down; do more
  spikes first.
- The implementation is exposing genuine surprises the planner missed.
  This is information; the planner should incorporate it.
- The external surface is drifting (a third-party API changed; the data
  shape moved). The contract isn't wrong; the world is.

Each diagnosis points to a different remediation. The metric doesn't
distinguish them on its own -- a human reads the recent failed-survival
cards and decides. But the metric tells you *when to look*.

### 6.3 Interpretation guardrails

- The survival rate is meaningless with n < 10 cards in the slice. The
  read API surfaces the n alongside the rate and refuses to color-code
  ("good", "bad") below threshold.
- The metric is asymmetric: a card that survives unchanged is genuinely
  a win; a card that needed amendments is *information*, not failure.
  The metric shouldn't be used to discipline the planner against
  contract drift; it should be used to detect *systematic* drift.
- Spikes (work_type=spike) are excluded from contract survival
  calculations by construction: spikes are exploratory and AC is
  expected to evolve.

---

## 7. Review cost model: human review as the binding constraint

### 7.1 The framing

Treat human review the way the rest of the system treats agent compute:
a real, variable cost that must appear in every quote. Not a flat
per-card overhead. Not zero.

The chunk 4 merge gate routes by tier:
- tier 1-2: auto-merge (zero human review)
- tier 3-4: sibling review (LLM, but human-on-callback when sibling defers)
- tier 5-6: human review (always)

So the expected review cost per card is a function of (work_type, tier,
gate_outcome, diff_size). Not a flat rate.

### 7.2 The function

```
review_seconds(wt, tier, gate, lines) =
    base(wt, tier, gate) + lines_per_minute_inverse(wt) * lines
```

Where:
- `base(wt, tier, gate)` is the constant overhead per PR (reading the
  card body, opening the PR page, deciding to review now vs later).
  Higher for human gates than for sibling gates.
- `lines_per_minute_inverse(wt)` is the per-line cost. Refactors review
  fastest (mechanical changes); features review slowest (semantic
  validation needed); migrations sit between.

Both constants are learned from the ledger (section 8.2) -- they're not
hardcoded.

**Design decision: linear in lines, not log.** Empirically, review time
grows roughly linearly with diff size up to about 500 lines, then plateaus
(reviewers stop reading carefully and rubber-stamp). The ledger should
log when a PR exceeds the plateau and treat the review time as a *lower
bound* on what the cost should be -- because the reviewer probably
didn't do a real review. Flag with `incomplete_metrics=True` on the row.

### 7.3 Sibling-reviewer vs human-reviewer cost

The two costs are tracked separately in the same row:

- `sibling_review_wall_seconds`: time from `pr_opened_at` to sibling
  decision (chunk 5 marker `at` timestamp).
- `human_review_wall_seconds`: time from `pr_opened_at` to human merge,
  for human-gated PRs; or to human override, when a sibling defers.

The quoting model (section 9) uses whichever is nonzero. For a quoted
card we don't yet know which gate it will hit; the read API returns the
expected value as a weighted sum:

```
E[review_seconds] = P(gate=auto) * 0
                  + P(gate=sibling) * E[sibling_review_seconds | wt, tier, lines]
                  + P(gate=human)   * E[human_review_seconds   | wt, tier, lines]
```

The gate probability comes from historical gate-outcome frequency at the
same `(work_type, tier)`.

### 7.4 Review as the binding constraint

For PCS quoting, a card with 5 minutes of agent-time and 4 hours of
human-review-time is a 4-hour card. Always quote against the slowest
link in the chain. The ledger surfaces this directly: the read API
returns `quotable_hours = max(build_hours, review_hours) + rework_hours`
with both components broken out so the operator can see which is
binding.

This is also why the auto-merge trust signal (section 8) matters --
the only way to make review *not* binding is to auto-merge more cards,
which is only safe if auto-merge stays trustworthy.

---

## 8. The empirical estimate model

### 8.1 Goal

Given `(work_type, tier)`, return:
- A point estimate (P50) for each of: agent_wall_seconds,
  executor_tokens, review_seconds, rework_cycles.
- A defensive estimate (P90) for the same fields.
- The sample size driving the estimate.

The estimate must be reasonable at n=0 (cold start) and tighten as n
grows.

### 8.2 Bayesian shrinkage rationale

Pure empirical percentiles are unstable at low n -- a single 12-hour
outlier card pollutes the P90 for a year. Pure priors ignore observed
data.

Shrinkage blends the two: the estimate at low n is dominated by the
prior; at high n it converges to pure empirical. Specifically:

```
weight_empirical = n / (n + k)
weight_prior     = k / (n + k)

estimate = weight_empirical * empirical_value
         + weight_prior     * prior_value
```

where `k` is the shrinkage constant (default 5). At n=0 the estimate is
the prior; at n=5 it's a 50/50 blend; at n=50 it's 90% empirical.

**Design decision: k=5 default.** Below n=5 the empirical signal is
noise; above n=50 the prior is irrelevant. k=5 puts the crossover where
it matters. Tunable per work_type when domain knowledge says so (e.g.
`docs` cards have tighter variance than `feature` cards; their k can be
lower).

### 8.3 What's the prior?

Three priors are layered, narrowest to broadest, with the narrowest
available used:

1. **Per (work_type, tier).** Whatever the table currently holds.
   Available once any card of this (work_type, tier) has finished.
2. **Per tier only.** Aggregate across all work_types at this tier.
   Available much sooner; reasonable when the new work_type doesn't
   exist in the data yet.
3. **Global cold-start prior.** Hardcoded defaults per tier, baked into
   the migration. Used only on a truly empty store.

The global cold-start prior is documented as a YAML file at
`runner/templates/metrics_priors.yaml` so an operator can hand-edit it
for their project (e.g. "our tier 4 cards typically take 6 hours, not
the global 3").

### 8.4 Recalibration schedule

The `metric_estimates` cache is refreshed when **any** of:

- A card transitions to a terminal state (incremental update for that
  `(work_type, tier)` pair).
- The CLI `cards-runner stats --recalibrate` is invoked.
- The daemon boots (full re-run; cheap with small data).

Recalibration touches only the cache table, never the `card_metrics`
rows.

### 8.5 Why percentiles, not mean+stddev

Card durations are heavy-tailed: most cards finish quickly, a few hang
for an order of magnitude longer. Mean+stddev under heavy tails is
deceptive (the mean drifts toward outliers; the stddev becomes huge).
Percentiles are robust. P50 + P90 captures the "typical case" and the
"defensive case" without modeling the tail explicitly.

---

## 9. Auto-merge trust signal

### 9.1 Definition

A card that was auto-merged (`merge_gate=auto`) is "trusted" if:

- No follow-up bugfix card cites it via `regresses:` within the next
  `cfg.regression_window_days` (default 14), AND
- No manual revert touched its merge commit, AND
- No card cites it as the cause of a `change_request:` (a sibling card
  amendment that traces back to this card's contract).

Otherwise it counted as a regression.

### 9.2 Rolling-window aggregation

The signal is the regression rate over the last N auto-merged cards
(default N=20, configurable):

```
auto_merge_regression_rate =
    count(auto_merged_cards_in_window where regression=True)
  / count(auto_merged_cards_in_window)
```

Returned alongside:
- absolute auto-merge count in window
- average days-to-regression for regressing cards
- top 5 regressing cards (for direct inspection)

### 9.3 What changes when trust dips

The signal is *advisory*. The operator decides whether to demote
auto-merge to sibling-review (tighten `pr_gate_enabled` or flip
`amendment_reviewer.auto_edit_ac` off). The runner does NOT
autonomously demote; that's a closed-loop trust-driven gate change and
out of scope per section 2.

The ledger does, however, emit a `metrics_events.jsonl` entry of kind
`trust_threshold_crossed` when the rate crosses a configurable threshold
(default 0.15) so a downstream alerting system can pick it up.

### 9.4 Bugfix card tagging

For this to work, a bugfix card MUST stamp the parent card it regresses:

```yaml
work_type: bugfix
regresses:
  - b042-03-add-rate-limit         # one or more parent card IDs
```

The planner does the stamping at card creation. The chunk 6c `doctor`
reports cards that violate "work_type=bugfix MUST have non-empty
regresses:" as warnings.

---

## 10. Quoting model

### 10.1 The composition

Given a card description (work_type + tier + expected diff size), the
read API returns:

```
quote = {
  "build_hours":     {p50, p75, p90},  # agent_wall_seconds + verifier
  "review_hours":    {p50, p75, p90},  # human_review_wall_seconds, weighted
                                       #   by gate-outcome probability
  "rework_hours":    {p50, p75, p90},  # rework_cycles * mean per-cycle cost
  "total_hours":     {p50, p75, p90},  # sum of the above, percentiles
                                       #   recomputed (not summed) because
                                       #   tail correlations exist
  "n_samples":       integer,
  "prior_weight":    float in [0,1],
  "confidence":      "low" | "medium" | "high",
}
```

`confidence` is a composite of n_samples and prior_weight:
- `low`: n < 5 or prior_weight > 0.7
- `medium`: 5 <= n < 30
- `high`: n >= 30 AND prior_weight < 0.3

PCS quoting practice: always quote the P75, never the P50. Internal
roadmapping: use P50 for the central estimate, surface P90 alongside.

### 10.2 What the quote IS NOT

The quote is not:

- Token cost in USD. (That's a separate read; the ledger has it but the
  PCS quote does not lead with it because clients don't price agent
  tokens.)
- Calendar time. Wall-clock to delivery includes queue time, review
  scheduling, multi-card concurrency, none of which the per-card row
  models. A second layer that combines per-card quotes into a calendar
  schedule is a future spec.
- A commitment. The quote carries variance bands for a reason.

### 10.3 Composing percentiles

Naively summing P75s of three components overcounts the tail (because
not every component goes to its 75th percentile simultaneously). The
read API recomputes the total's P50/P75/P90 from the joint distribution
when card-level data is sufficient, falling back to a quadrature-sum
correction (`sqrt(sum_of_squared_p_deltas)`) when not.

This is a footgun worth designing carefully; the implementation should
clearly document which mode is in effect for any returned quote.

---

## 11. Read API

### 11.1 Python surface

```python
from cards_runner.metrics import (
    quote,            # quote a hypothetical card
    estimate,         # raw estimate for a (work_type, tier)
    contract_survival,
    auto_merge_trust,
    recalibrate,
)

q = quote(work_type="feature", tier=3, expected_lines=200)
# -> Quote dataclass with the section 10.1 shape

estimate(work_type="refactor", tier=2)
# -> Estimate dataclass: percentiles + n_samples + prior_weight

contract_survival(work_type="feature", window_cards=30)
# -> ContractSurvival dataclass: rate + n + window + per-card details

auto_merge_trust(window_cards=20)
# -> TrustSignal dataclass: rate + count + top-regressing cards
```

### 11.2 CLI surface

A new `cards-runner stats` subcommand (orthogonal to `doctor`):

```
cards-runner stats quote --work-type feature --tier 3 --lines 200
cards-runner stats estimate --work-type refactor --tier 2
cards-runner stats survival --work-type feature
cards-runner stats trust
cards-runner stats recalibrate            # force a recompute
cards-runner stats dump --json            # full state dump
```

All commands accept `--json` for machine-readable output (chunk 6c
`doctor` precedent).

### 11.3 What `cards-runner stats trust` prints

The most operationally important readout. Example:

```
auto-merge trust signal (last 20 auto-merged cards, window 14d):
  regression rate: 2/20 = 10%
  threshold:       15% (advisory; runner does NOT auto-demote)
  mean days to regression: 6.5
  top regressing cards:
    b031-04-add-cache (auto-merged 2026-05-10, regressed via b033-01 on 2026-05-13)
    b035-02-add-retry (auto-merged 2026-05-15, regressed via b037-01 on 2026-05-20)
  recommended action: trust still good; no change needed.
```

---

## 12. Validation and migration plan

### 12.1 Schema migration

One migration in one chunk:

1. Add `work_type` column to `cards` table; default `unknown`.
2. Create `card_metrics` table.
3. Create `metric_estimates` table.
4. Touch `signals/metrics_events.jsonl` (created on first append).

### 12.2 Backfill

A one-shot CLI: `cards-runner stats backfill`. For each existing card:

- Stamp `work_type=unknown` if not set.
- Compute every derivable metric from the existing event log / store.
- Mark `incomplete_metrics=True` for anything that can't be derived.

The estimator excludes incomplete-metric rows from its training set, so
backfill cards don't poison the empirical estimates. They're surfaced
in `dump --json` for human re-tagging if the operator wants to clean
them up.

### 12.3 Verification before going live

Two checks before the ledger is considered authoritative:

1. **Audit-log replay.** A test script reads
   `metrics_events.jsonl` and rebuilds `card_metrics` from scratch.
   The rebuilt rows must match the live table modulo idempotent
   re-writes.
2. **Quote sanity.** For a sample of completed cards, the read API
   should return a quote whose P50 is within 20% of the actual
   outcome. (Below that bar, the estimator is mis-calibrated; treat
   any quotes it returns as low-confidence until enough data accrues
   to recalibrate.)

---

## 13. Open design questions

These are explicit "Drew to decide" items.

### 13.1 Does `contract_survived` count an approved auto-edit (chunk 6a) as drift?

A chunk-6a `auto_edit_ac` approve-then-splice routes the card back to
`backlog`, modifies AC, and re-runs. Strict reading of section 6.1 says
the contract was amended -> contract_survived=False. Generous reading
says the *automation* handled it without human intervention, which is
still a speculative-parallelism win.

Recommendation: count it as False. The point of contract-survival is
"did we predict the contract right up front", not "did the runner
handle drift well". The auto_edit_ac path handling drift is a separate
signal (and it's already counted via the chunk 6d history JSONL).

### 13.2 How does the ledger handle multi-tenant projects?

Multi-tenant is supported in the card store schema but largely unused.
The ledger's PK is `(card_id, tenant_id)` so the data model is ready.
The read API filters by tenant_id; a default that aggregates across
tenants is wrong for quoting (you'd quote your client a number that
includes another client's slower work).

Recommendation: read API requires explicit `--tenant` flag; defaults to
the cards-runner default tenant. Cross-tenant rollup is the
out-of-scope item from section 2.

### 13.3 What's the relationship to the verifier_cascade_history?

The chunk 3 verifier already records cascade escalations. Some of those
are rework-equivalent (a tier escalation often means the executor's
first answer was wrong). The ledger could either:

- Count verifier cascade escalations as rework cycles (more aggressive
  rework signal).
- Treat them as separate from card-level rework (cascade is internal
  to one attempt, not a re-attempt).

Recommendation: treat them as separate. A cascade is a within-attempt
correction; rework is a between-attempt one. They measure different
things. Both belong in the read API output but in distinct fields.

### 13.4 Per-card actual vs estimated comparison

The ledger has all the inputs to do "estimated 3 hours, actual 4.5
hours" comparisons per card. Should this be a first-class metric?

Recommendation: yes, but as a derived read (`cards-runner stats variance
--work-type feature`), not a stored column. Variance over time is the
self-calibration signal -- if estimates systematically run 30% low,
the priors need adjusting.

### 13.5 What's the unit for `agent_wall_seconds`?

Wall-clock between `started_at` and `finished_at` includes time the
worker spent waiting on a tool call, time the cost governor halted it,
and time the OS scheduled it. CPU-seconds would be cleaner.

Recommendation: keep wall-clock. It's what a client experiences. CPU
is misleading because parallel agent work scales differently.

---

## 14. Sequencing if approved

Suggested chunk decomposition (each is independently mergeable; no
inter-dependencies beyond the obvious order):

1. **Schema + work_type field.** One migration; the `cards.work_type`
   promoted column + the `card_metrics` and `metric_estimates` tables.
   Includes the planner-side enforcement and the doctor read.
2. **Write surface in the runner.** Wire each section 5.2 author to
   actually write its field on its trigger. Include the
   `signals/metrics_events.jsonl` append.
3. **The estimator.** `cards_runner.metrics.estimate` +
   shrinkage math + the cold-start prior YAML.
4. **Quote read API + CLI.** `quote()`, `cards-runner stats quote`.
5. **Contract-survival.** Detector + read API + CLI.
6. **Trust signal.** Rolling-window aggregator + threshold-crossed
   event + CLI.
7. **Backfill.** One-shot CLI + the verification checks (section 12.3).

Steps 1-2 are the load-bearing changes. Steps 3-7 layer on top and can
land independently as the use cases mature. PCS-quoting need is
realistically covered after step 4; roadmapping use is covered after
step 3; trust signal is covered after step 6.

---

## 15. DESIGN READY FOR REVIEW

Done items:
- [x] Problem motivated against PCS quoting, roadmap self-calibration,
      and trust-signal use cases (section 1).
- [x] Scope and non-goals enumerated (section 2).
- [x] Per-card data model + audit log + estimate cache (section 3).
- [x] Work-type taxonomy with nine canonical categories (section 4).
- [x] Storage decisions documented with reasoning (section 5).
- [x] Contract-survival metric defined with interpretation guardrails
      (section 6).
- [x] Review treated as binding-constraint variable cost (section 7).
- [x] Bayesian-with-shrinkage estimator with cold-start prior (section
      8).
- [x] Auto-merge trust signal with rolling window and operator-advisory
      behavior (section 9).
- [x] Quoting model with explicit "this is NOT" boundaries
      (section 10).
- [x] Python + CLI read API surfaces (section 11).
- [x] Migration + backfill + verification plan (section 12).
- [x] Open questions called out for Drew's decision (section 13).
- [x] Implementation sequencing with independent-merge ordering
      (section 14).

Awaiting:
- [ ] Drew's review and approval before any code lands.
- [ ] Resolution of the five open questions in section 13.
- [ ] Confirmation on the priors in `metrics_priors.yaml` (or a
      decision to defer the file until empirical data exists).

When approved, the suggested branch sequence in section 14 turns into
a stack of implementation PRs on top of `main`. None of them depend
on the chunk 6 stack landing first; the work is fully independent.
