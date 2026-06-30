# Confidence-Driven Merge Gate

Status: DESIGN. Not implemented. Awaiting Drew's approval before any code
or schema changes land.

Author: planner pass (Claude, Opus 4.7)
Created: 2026-05-24
Branch (suggested): `feature/confidence-driven-merge-gate`
Depends on:
- `RUNNER_CONTRACT.md` "Merge gates" (the tier-based routing this
  replaces) and "Cold-read verification" (the v1.3 two-path verifier this
  consumes).
- `docs/design/throughput_metrics_ledger.md` (merged via PR #14).
  This spec writes into the ledger and reads from it for calibration.
- The chunk 5 `daemon/sibling_reviewer.py` and its `ReviewerDecision`
  shape (`decision` + `reasoning` + `confidence` + project config
  `sibling_reviewer_enabled` / approval-confidence-floor). The
  confidence gate reads `ReviewerDecision` directly. When a project has
  `sibling_reviewer_enabled: false` (or the marker is absent because the
  reviewer has not yet run), the gate degrades gracefully and the
  sibling-agreement signal contributes zero.

---

## 1. Problem and goal

### 1.1 What Drew wants

Drew's words: *"I want to automate as much as possible. I should only need
to PR the most intense merges, where Opus is feeling iffy."*

Today the runner gates merges on static tier (chunk 4 `MergeGate`):
tier 1-2 auto-merge, tier 3-4 sibling review, tier 5-6 human review,
with `pin_required: true` always pinning to human. Tier is a planning-time
classification of *expected* difficulty. It does not respond to what
actually happened during the card.

The goal: route on **runtime verifier confidence** instead. A tier 5
card the verifier handled cleanly with no surprises should be able to
auto-merge. A tier 2 card the verifier squeaked past with three retries
and a sensitive-area touch should escalate to Drew. Tier becomes one
input among several to the confidence signal, not the routing key.

### 1.2 The honest part

An LLM's *raw stated* confidence is not well-calibrated. "Opus says
0.92" is not the same as "this change has a 92% chance of being good."
LLM self-reports of confidence are systematically over-confident in
familiar territory and systematically under-confident in unfamiliar
territory, with no public ground-truth telling us where the
crossover is for this codebase.

Therefore: this spec does NOT take the verifier's stated confidence
value as the routing signal. The verifier's stated confidence is one
ingredient (and a weak one) in a composite signal built from several
*more reliable* sources -- sibling-reviewer agreement, structured
risk-factor enumeration, diff size and blast radius, test results,
sensitive-area touches, runtime cascade history, AC quality. The
gate threshold that maps the composite signal to auto/sibling/human
is then **calibrated from measured ledger data**, not from
intuition.

### 1.3 What "good" looks like

Two metrics together:

1. **Operator interrupt rate.** Fraction of cards that reach Drew for
   merge. Lower is better, *if* and only if (2) holds.
2. **Per-bucket regression rate.** For cards auto-merged at confidence
   band X, the fraction that later regress (per the ledger's section 9
   regression definition). Must stay below `regression_target_per_bucket`
   (default 0.05) for every band the operator authorizes auto-merge on.

A system that drives (1) down without watching (2) is a system that
ships regressions faster. The whole point of the ledger integration
(section 7) and the conservative ramp (section 9) is to keep (1) and
(2) honest with each other.

---

## 2. Scope and non-goals

### In scope

- A composite confidence signal built from structured inputs the
  runner can actually measure -- not from LLM self-report alone.
- A gate decision module (`daemon/confidence_gate.py`) that consumes
  the signal and returns `auto` / `sibling_review` / `human_review`.
- A "hard escalator" surface: a small set of conditions that bypass
  the numeric score entirely and force `human_review`. These exist
  because some properties (e.g. schema migration touched) are
  categorically "Drew looks at this" regardless of how confident the
  verifier is.
- A shadow-mode phase (the runner computes confidence + decision but
  does not act on it) so we collect calibration data before changing
  routing behavior.
- A ledger schema extension that stores the confidence inputs and the
  gate decision per card.
- A calibration loop that compares auto-merged outcomes to the
  decision signal and surfaces per-bucket regression rates, calibration
  monotonicity, and a recommended phase action.
- A conservative ramp protocol with explicit phase advancement
  controlled by the operator, not the runner.
- A kill-switch behavior: if measured regression rate exceeds a
  multiple of the target, the runner falls back to chunk-4 tier-based
  routing automatically and emits an alert event.

### Explicit non-goals

- **No autonomous phase advancement.** The system can recommend
  widening the auto band; only Drew flips the knob. (Mirrors the
  ledger spec's section 9 "advisory not autonomous" stance for the
  same reasons.)
- **No new merge-status enum values.** The chunk 4 `merge_status` set
  (`pending`, `open`, `merged`, `requires_review`, `conflict`,
  `blocked`) covers everything; we only change how the gate *picks*
  among them.
- **No prediction beyond the immediate merge decision.** The
  confidence signal answers "is this safe to merge auto?" only. It
  does not predict effort, downstream impact, future maintenance cost.
  Those are ledger questions (section 6 contract-survival, section 10
  quoting).
- **No fundamentally new LLM call.** The signal is computed from data
  the runner already has by the end of the v1.3 verifier pass plus
  some `gh` / `git` reads on the worktree. No extra Sonnet or Opus
  invocation for the confidence judgment itself.
- **No model-trained classifier yet.** The composition is a documented
  linear formula with explicit weights. Once enough ledger data
  accumulates (the ramp's phase 3+), swapping the linear formula for
  a fitted logistic regression is a small focused chunk.
  Doing it before then is fitting noise.
- **No cross-tenant calibration data sharing.** Same scope discipline
  as the ledger.

---

## 3. The confidence signal

The hardest part of this spec, treated explicitly. Read section 3.1
before any of the formula.

### 3.1 Why we cannot just trust the verifier's stated confidence

The v1.3 subjective evaluator returns a per-item `confidence` in
`[0.0, 1.0]`. It is the model's self-report. That number drives the
cascade (haiku -> sonnet -> opus on confidence below threshold) and
the standup-review escalation when even Opus stays below threshold.

For *cascade routing* it works fine because the cascade only needs a
relative signal: "is this model uncertain enough that the next tier is
worth trying?" The model knows its own ignorance relative to the next
tier roughly OK.

For *gate routing* the requirement is different: we need an
*absolute* probability that the change is correct and safe to ship.
That is a calibration question, and:

- LLMs are systematically over-confident in familiar territory.
  Opus saying 0.92 on a tax-calculation card looks like "92% chance
  of being right". In a domain Opus has seen a thousand times in
  training, 0.92 is often correct. In a domain Opus has seen ten
  times, 0.92 is *still* the number, but the underlying probability
  is much lower.
- LLMs are systematically under-confident in genuinely novel
  territory because they pattern-match "this is unusual" to "I should
  hedge".
- We have no public ground-truth calibration of `claude-opus-4-7`
  for the agile-cards problem space. Anything we trust about that
  number is faith, not measurement.

Therefore the spec uses verifier-stated confidence as ONE input among
several, capped at a low weight, and only as a SOFT signal. The HARD
escalators do not read it at all. The historical-floor adjustment
(section 3.5) is what gives us absolute calibration, eventually, from
ledger measurement.

### 3.2 Two layers: hard escalators and soft signals

The signal has two layers:

```
if any hard_escalator fires:
    decision = human_review
    confidence_score = recorded but unused for routing
else:
    decision = pick_band(confidence_score)
```

Hard escalators are categorical "Drew sees this regardless" conditions.
Soft signals combine into a numeric `confidence_score` in `[0.0, 1.0]`
that maps to a band.

### 3.3 Hard escalators (default conservative set)

Any one of these fires the human gate. The list is YAML-configured
per project; the project may remove items (per ramp phase) but the
defaults below are the day-1 set.

| escalator | condition | rationale |
|---|---|---|
| `pin_required` | card frontmatter `pin_required: true` | chunk 4 contract: stakes=high always pins. Unchanged. |
| `subjective_cascade_opus_used` | v1.3 cascade reached opus tier for any subjective item | if Haiku and Sonnet both ducked, Drew looks. |
| `sensitive_path_touched` | any file in diff matches project `sensitive_paths:` globs | auth, crypto, migrations, payments, IAM. Defaults in 10.2. |
| `schema_migration_in_diff` | diff includes `migrations/*`, `schema*.sql`, `alembic/*` (project-configurable globs) | DDL is irreversible enough to warrant a look. |
| `new_external_dependency` | `package.json` / `pyproject.toml` / `Cargo.toml` / `go.mod` shows an added entry | supply-chain surface. |
| `sibling_disagreement` | sibling reviewer present AND verdict disagrees with primary verifier | section 3.4 |
| `large_diff` | `lines_added + lines_removed > project.large_diff_threshold` (default 500) | matches the ledger section 7.2 review-time plateau. Beyond the plateau the verifier is also less reliable. |
| `executor_change_request_unresolved` | card body has a `change_request:` block with no recorded decision | contract drift the runner has not fully reconciled. |
| `verifier_incomplete_metrics` | any verifier handler returned with `incomplete_metrics=True` evidence | we are missing the signal we would need. Default to escalation. |
| `risk_factor_high_severity` | verifier emitted a structured risk factor with `severity: high` | section 3.6 |
| `regression_rate_alarm_active` | the ledger's auto-merge regression rate for this `(work_type, tier)` bucket has crossed the alarm threshold | kill-switch (section 9.4) |

Each hard escalator that fires is recorded in the card's
`merge_escalators:` frontmatter list as `{kind, evidence, at}` so the
gate decision is fully auditable.

**Design decision: hard escalators are categorical, not weighted.** A
weighted aggregation can paper over "this card touched the secrets
file" with three positive signals. The hard escalator list says: some
properties are not negotiable. Drew sees them, period.

**Design decision: the hard escalator list shrinks as ramp phases
advance, but never to empty.** Even at the most relaxed phase, at least
`pin_required`, `sensitive_path_touched`, `schema_migration_in_diff`,
and `regression_rate_alarm_active` remain. These are not "trust
calibration" decisions; they are policy. Phase advancement adjusts the
band thresholds and demotes some *soft* signal weights, not the policy
escalators.

### 3.4 Sibling-reviewer agreement

The chunk 5 sibling reviewer (`daemon/sibling_reviewer.py`) emits a
`ReviewerDecision` per `requires_review` PR with shape
`{decision: approve | request_changes | comment, reasoning, confidence}`
and writes it to `signals/sibling_reviews/<card_id>.json`. The
confidence gate reads that marker file (or an equivalent in-store
record once chunk 6+ promotes it) and maps to the soft signal:

| sibling state | primary verifier verdict | confidence-gate effect |
|---|---|---|
| `sibling_reviewer_enabled: false` or marker absent | n/a | soft signal `sibling_agreement` = neutral (no contribution) |
| sibling `decision: approve` | pass | soft signal contributes +0.20 (capped) |
| sibling `decision: request_changes` | either | **hard escalator** `sibling_disagreement` fires |
| sibling `decision: comment` | pass | soft signal contributes 0; primary verifier alone |
| sibling enumerated a high-severity risk factor (section 3.6) the primary did not | pass | soft signal -0.10 per high-severity item the primary missed |

**Why sibling agreement is the highest-weight soft signal.** Two
independently-prompted reviewers reaching the same verdict is the most
information-dense signal the system can produce. Disagreement is
load-bearing: if the sibling sees something the primary missed, Drew
should see it.

**Ordering with the chunk 5 sibling flow.** Today, chunk 5's sibling
reviewer only runs on `requires_review` cards (tier 3-4 under the
chunk-4 routing). Under the confidence gate, "is this card going to
sibling_review?" is itself an output, not an input. The clean ordering:
the confidence gate first computes a tentative outcome ignoring the
sibling-agreement signal. If the tentative outcome is `auto` or
`human_review`, the gate skips sibling-review and routes directly.
If the tentative outcome is `sibling_review`, the sibling reviewer
runs as today; the gate's final decision is taken from the
sibling's `ReviewerDecision` (approve -> auto-merge promotion,
request_changes -> human escalation, comment -> stay in
requires_review for a human).

This means the soft `sibling_agreement` signal contributes only on
*re-decisions* of cards that have already had at least one sibling
pass -- for example, a card that came back to the gate after a
rework cycle and now has a prior sibling-review marker on file.
Greenfield first-pass cards get the +0.20 contribution only from a
*previous* sibling decision on a prior attempt; not having one is
not penalized.

**Reading the sibling's stated `confidence` value.** The
sibling-reviewer's confidence is, like the primary verifier's, an
LLM self-report. Per section 3.1 we treat it as a weak signal: it
already feeds the chunk 5 approve-or-degrade floor inside the
sibling reviewer itself (the reviewer downgrades `approve` to
`comment` below its own confidence floor). The confidence gate
reads the *decision* (approve/comment/request_changes) but does
NOT directly read the numeric confidence; the decision is the
calibrated output of the chunk 5 reviewer's own floor logic.

### 3.5 Historical-floor adjustment (the ledger feedback loop)

Every (work_type, tier) bucket has a rolling 30-day regression rate
computed from the ledger. The confidence_score is adjusted by a
multiplicative *historical floor*:

```
adjusted_score = raw_score * (1 - alpha * bucket_regression_rate)
```

Where `alpha` is the floor sensitivity (default 2.0). Examples:

- Bucket with 0% rolling regression rate: `adjusted = raw * 1.00`.
- Bucket with 5% rolling regression rate: `adjusted = raw * 0.90`.
- Bucket with 15% rolling regression rate: `adjusted = raw * 0.70`.

A bucket with a 25% regression rate trips the
`regression_rate_alarm_active` hard escalator (section 3.3) before
this multiplication ever matters; the multiplicative floor only
matters in the band where the bucket is still in service but
performing worse than its peers.

**Design decision: multiplicative, not subtractive.** Subtractive
adjustment punishes high-confidence cards as much as
mid-confidence cards in absolute terms. Multiplicative compresses
the band proportional to recent track record, which matches the
real behavior we want: "if this bucket has regressed a lot lately,
treat *all* its 'high confidence' cards with proportionally more
suspicion."

**Design decision: bucket = (work_type, tier).** Not per-card
features (over-fits to noise) and not global (a single bad bucket
should not poison every other bucket's gate). The ledger spec
already settles on `(work_type, tier)` as the slicing key; same
here.

**Bucket cold-start.** A bucket with fewer than
`floor_calibration_n_floor` completed auto-merges (default 10) uses
the *tier-aggregate* regression rate as a backstop, falling back to
the global runner-wide rate if even the tier aggregate has < 10
data points. Same layered-prior pattern the ledger uses in section
8.3.

### 3.6 The verifier's risk-factor enumeration contract (new)

The v1.3 verifier currently returns:

```python
VerifierResult(
    overall_status,       # pass | fail | needs_standup_review
    items,                # tuple of ItemResult
    cascade_history_appendix,
    standup_reason_items,
)
```

v1.3.1 adds one structured field:

```python
risk_factors: tuple[RiskFactor, ...]
```

Each `RiskFactor`:

```python
@dataclass(frozen=True)
class RiskFactor:
    kind: str                 # enum below
    severity: str             # "low" | "medium" | "high"
    location: str | None      # file:line if applicable
    description: str          # one-line human reason
    source_item_idx: int | None  # which AC item raised it, if any
```

`kind` enum (extensible; gate consumes only the entries it knows):

| kind | example |
|---|---|
| `external_call_added` | new HTTP/SDK call introduced |
| `guard_removed` | conditional check deleted that previously gated a code path |
| `raw_sql` | new `execute(string)` style SQL string |
| `string_eval` | new `eval` / `exec` / template-injection surface |
| `crypto_change` | edited crypto config / cipher / random-number generator |
| `error_swallowed` | new `except:` / `catch` with no rethrow |
| `concurrency_change` | new thread/asyncio/lock-related code |
| `permission_change` | new chmod / acl / role assignment |
| `unverified_assumption` | AC verified the behavior under one condition; verifier suspects others matter |
| `incomplete_test_coverage` | diff added a function the diff does not also test |
| `dep_pin_loosened` | version constraint widened |

**Who emits risk factors.** Both phases of v1.3 verification:
- Deterministic handlers may emit risk factors as part of their evidence
  (e.g. `command` handler returning a non-zero coverage delta).
- Subjective evaluator MUST emit risk factors as part of its structured
  output. The prompt is updated to require the model to list any
  it observed even when the items pass.

**How the gate consumes risk factors.**
- Any `severity: high` is a hard escalator (`risk_factor_high_severity`).
- Each `severity: medium` contributes a soft signal of `-0.07` (capped
  at `-0.25` aggregate).
- Each `severity: low` contributes `-0.02` (capped at `-0.10`
  aggregate).
- The kinds themselves are not weighted; the *severity* is the carrier.
  This keeps the kind enum extensible without re-tuning weights every
  time a new kind is added.

**Design decision: severity is the verifier's call, not the gate's.**
The verifier sees the code; it is the one that should classify "this
new HTTP call is to a known-internal localhost vs a brand-new external
host". The gate just consumes the severity tag. This keeps the gate
module ignorant of code semantics.

### 3.7 Soft signals: the formula

Composite `raw_score` starts at 0.50 (uncertain) and accumulates:

```python
raw_score = 0.50

# Verifier path quality
raw_score += 0.10 if all_deterministic_first_try else 0.00
raw_score += 0.05 if subjective_phase_cleared_at_haiku else 0.00
raw_score += 0.02 if subjective_phase_cleared_at_sonnet else 0.00
# (opus tier is already a hard escalator; no soft contribution)
raw_score -= 0.05 * len(cascade_history)            # runtime tier climbs
raw_score -= 0.05 * rework_cycles                   # between-attempt loops

# Sibling agreement (when present)
raw_score += 0.20 if sibling_agrees_pass else 0.00
# (sibling disagreement is a hard escalator)

# Diff signals
raw_score -= 0.02 * (diff_total_lines // 100)       # capped at -0.20
raw_score += 0.10 if diff_is_test_only else 0.00
raw_score += 0.05 if diff_within_planner_declared_scope else 0.00

# Risk factors (section 3.6)
raw_score -= 0.07 * num_medium_risk_factors          # capped at -0.25
raw_score -= 0.02 * num_low_risk_factors             # capped at -0.10

# Verifier-stated confidence (low weight, see 3.1)
raw_score += 0.05 * (verifier_confidence - 0.85) / 0.15   # only the part above 0.85, capped at +0.05

# Clamp
raw_score = max(0.0, min(1.0, raw_score))

# Historical-floor adjustment (section 3.5)
confidence_score = raw_score * (1 - alpha * bucket_regression_rate)
confidence_score = max(0.0, min(1.0, confidence_score))
```

**Design decision: a linear formula with documented weights, not a
trained model.** Until we have hundreds of auto-merged cards with
ledger-tracked outcomes, fitting a model is fitting noise. A linear
formula is auditable, debuggable, and explicit about what is being
weighed. Once the ledger has enough data (section 9.3 phase 3
threshold), a fitted logistic regression replaces the linear formula
in a focused follow-on chunk; the inputs do not change.

**Design decision: starting bias is 0.50.** A card with no signal
either way lands in the sibling-review band on day-1 phase 2
thresholds. This is the conservative starting position: ambiguous
cards do not auto-merge.

**Design decision: weights are starting guesses, the ramp checks
calibration before widening.** The actual values matter less than the
fact that they are *measured*. Phase 1 shadow mode (section 9.1)
exists specifically to find the wrong weights before they affect
routing.

---

## 4. Gate logic

### 4.1 The decision

```python
def decide(card, verifier_result, sibling_result, diff_stats,
           bucket_history) -> GateDecision:

    escalators = find_hard_escalators(card, verifier_result,
                                       sibling_result, diff_stats,
                                       bucket_history)
    if escalators:
        return GateDecision(
            outcome="human_review",
            confidence_score=compute_confidence_score(...),  # still recorded
            escalators=escalators,
            reason="hard_escalator",
        )

    raw = compute_raw_score(card, verifier_result, sibling_result,
                            diff_stats)
    score = raw * (1 - alpha * bucket_history.regression_rate)
    score = clamp(score, 0.0, 1.0)

    if score >= cfg.confidence_auto_threshold:
        outcome = "auto"
    elif score >= cfg.confidence_sibling_threshold:
        outcome = "sibling_review"
    else:
        outcome = "human_review"

    return GateDecision(
        outcome=outcome,
        confidence_score=score,
        raw_score=raw,
        escalators=[],
        reason="confidence_band",
        inputs=structured_inputs_dict,   # full audit
    )
```

The `GateDecision` is the structured object the ledger writes (section
7) and the runner acts on.

### 4.2 Phase 1: shadow mode

When `confidence_gate_mode = "shadow"`, the runner:

1. Runs the chunk 4 tier-based `MergeGate` as today and uses ITS
   decision for routing.
2. Runs the confidence-gate ALSO and stores the `GateDecision` plus all
   inputs on the card and in the ledger.
3. Emits a `metrics_events.jsonl` event of kind `gate_shadow_decision`
   with both the chunk-4 decision and the confidence-gate decision.
4. Does NOT route on the confidence-gate decision.

The point: collect calibration data without changing behavior. Section
9 details what graduates phase 1 to phase 2.

### 4.3 Phase 2+: live mode

When `confidence_gate_mode = "live"`, the runner uses the confidence
gate as the authority for routing. The chunk-4 tier-based logic
becomes the FALLBACK that activates only if the kill-switch (section
9.4) trips.

`pin_required: true` continues to force `human_review` regardless of
mode -- it is a hard escalator in both the chunk-4 logic and the
confidence-gate logic, with identical semantics. Cards with
`stakes: high` still pin (the chunk 4 contract is unchanged).

### 4.4 What replaces tier-based routing

The chunk-4 `MergeGate.decide_gate(points, pin_required) -> {auto,
sibling_review, human_review}` is no longer the authority in live
mode. It is wrapped by the new `ConfidenceGate` module which calls
into the chunk-4 logic ONLY as fallback. Tier (`points`) is still
used:

- As an input to `(work_type, tier)` bucket history.
- As an indirect input to several soft signals (a tier 6 card with a
  500-line diff is a different signal from a tier 1 card with the
  same diff -- the soft formula does not currently capture this; the
  bucket-history adjustment does, indirectly).
- As the kill-switch fallback when the confidence-gate is paused.

**Design decision: tier is NOT a direct soft-signal input.** The
intuition is "tier is supposed to be expected-difficulty; the
confidence signal is supposed to capture actual difficulty observed
at runtime". Double-counting tier in the soft formula creates a
self-reinforcing loop where high-tier cards never accumulate ledger
data because they keep getting routed to humans, which then keeps
their bucket history empty, which then keeps them routed to humans.
Bucket history alone is the right place for tier influence -- it
reflects actual outcomes, not planner intent.

### 4.5 Interaction with `awaiting_standup_review`

The v1.3 verifier's `needs_standup_review` outcome is upstream of the
gate. If the verifier could not reach a verdict, the card never
reaches the merge gate -- it goes to `awaiting_standup_review/` for
human resolution. The confidence gate operates only on cards the
verifier passed (`overall_status == "pass"`).

This is deliberate: the two failure modes are different.
`awaiting_standup_review` is "the verifier could not decide if the AC
was satisfied". `human_review` from the confidence gate is "the AC
was satisfied but the change is iffy enough that Drew should look at
it anyway". A card can in principle hit both (the verifier escalates
to standup-review, a human passes it, then the gate sees it and
escalates again because the diff is huge); the two surfaces stay
distinct so Drew can tell what kind of decision he is being asked to
make.

---

## 5. Integration with the v1.3 verifier

### 5.1 New `VerifierResult` field

```python
@dataclass(frozen=True)
class VerifierResult:
    overall_status: str
    items: tuple[ItemResult, ...]
    cascade_history_appendix: tuple[dict, ...]
    standup_reason_items: tuple[int, ...]
    risk_factors: tuple[RiskFactor, ...]    # NEW in v1.3.1
```

Backward compatible: a v1.3 verifier that returns `risk_factors = ()`
is treated as "verifier emitted no risk factors". The confidence
gate's risk-factor inputs are empty in that case; the card still gets
a confidence score, just without the risk-factor contribution.

### 5.2 Subjective evaluator prompt update

The subjective evaluator's structured-output schema gains one field
per top-level response (not per item -- the model emits risk factors
as a single list for the whole card):

```json
{
  "items": [ {"idx": 0, "result": "pass", "confidence": 0.92, "reasoning": "..."} ],
  "risk_factors": [
    {"kind": "external_call_added",
     "severity": "medium",
     "location": "src/payments.ts:42",
     "description": "new fetch() to api.thirdparty.com; not previously called from this module"}
  ]
}
```

The prompt explicitly asks the model: *"even if you marked every item
as pass, enumerate any code-level risks you noticed in the diff or the
evidence. Mark each as low / medium / high. We will NOT use this list
to flip your pass to a fail; we use it to decide who reviews the
merge."*

**Design decision: the model knows the risk-factor list will not
flip its pass/fail call.** Otherwise the model has an incentive to
under-report risks (every reported risk could "cost" it a pass).
Decoupling the two surfaces makes honest risk reporting cheap.

### 5.3 Deterministic-handler risk emission

Each deterministic handler MAY emit risk factors as part of its
`HandlerResult.evidence`:

```python
HandlerResult(
    passed=True,
    evidence={
        "stdout": "...",
        "risk_factors": [
            {"kind": "incomplete_test_coverage",
             "severity": "low",
             "description": "diff added 3 functions; 1 has no matching test"},
        ],
    },
)
```

Handlers are not required to emit risk factors -- most deterministic
handlers (`file_exists`, `file_absent`) have nothing meaningful to
contribute. The handlers that should emit risk factors over time
include:

- `command` handlers that wrap test runners (coverage delta, slow-test
  flag, deprecation warnings in stdout).
- `python_assert` handlers that find structural problems by accident
  (e.g. an assertion about API surface that quietly succeeds because
  the API was removed entirely).

This is a v1.3.2-and-later expansion surface; v1.3.1 ships with the
subjective evaluator path as the primary risk-factor source.

---

## 6. Integration with the chunk-4 `MergeGate`

The chunk-4 `MergeGate` is not removed. It is wrapped:

```
runner._dispatch_verifier (chunk 4)
  -> verifier.runner.verify_card -> VerifierResult
  -> if overall_status == "pass":
       confidence_gate.decide(...) -> GateDecision    # NEW
         -> branches:
            "auto"           -> merge_gate.apply with auto path
            "sibling_review" -> merge_gate.apply with sibling path
            "human_review"   -> merge_gate.apply with human path
       (the "apply" path is identical to chunk 4; only the chooser
        changes.)
```

In phase 1 shadow mode the wrapping is "tee" style: both gates run,
chunk-4 decides, both decisions are recorded, no behavior change.
In phase 2+ live mode the confidence gate becomes the authority and
chunk-4 is the fallback.

### 6.1 New module: `runner/src/cards_runner/daemon/confidence_gate.py`

Exports:

```python
class ConfidenceGate:
    def __init__(self, cfg: ConfidenceGateConfig, store: Store,
                 ledger: LedgerWriter):
        ...

    def decide(self, card: CardRecord, verifier_result: VerifierResult,
               sibling_result: SiblingResult | None,
               diff_stats: DiffStats,
               bucket_history: BucketHistory) -> GateDecision:
        ...

    def is_live(self) -> bool:
        """True when the runner should route on this gate's decision."""
        ...
```

`DiffStats` is built by a small helper that runs `git diff --numstat`
against `base_branch...card_branch` and parses the output.

`BucketHistory` is loaded from `card_metrics` + `metric_estimates`
via the ledger read API.

`SiblingResult` is built from the latest
`signals/sibling_reviews/<card_id>.json` marker when present, or is
`None` when the marker is absent (sibling reviewer disabled, has not
yet run on this card, or runs on a future tick per section 3.4
ordering).

### 6.2 `GateDecision` shape

```python
@dataclass(frozen=True)
class GateDecision:
    outcome: str                     # "auto" | "sibling_review" | "human_review"
    confidence_score: float          # in [0.0, 1.0]; recorded even if hard-escalator-driven
    raw_score: float | None          # pre-historical-floor; None if hard-escalator-driven
    escalators: tuple[str, ...]      # hard-escalator kinds that fired (empty if soft band)
    reason: str                      # "hard_escalator" | "confidence_band" | "fallback_chunk4"
    inputs: dict                     # structured audit dict; section 7.2 schema
    at: str                          # ISO timestamp
    bucket: tuple[str, int]          # (work_type, tier)
```

The full `inputs` dict is what makes the decision reproducible from
the ledger. It carries every soft-signal contribution, every
escalator's evidence, and the bucket-history values used. The ledger
writer (section 7) persists this directly into
`metrics_events.jsonl`; the `card_metrics` row carries the
summary fields.

---

## 7. Ledger integration

### 7.1 New `card_metrics` columns

| column | type | source | nullable | purpose |
|---|---|---|---|---|
| `gate_decided_at` | TEXT (ISO) | confidence_gate | yes | when the gate ran |
| `gate_outcome` | TEXT | confidence_gate | yes | `auto` / `sibling_review` / `human_review` |
| `gate_confidence_score` | REAL | confidence_gate | yes | composite score after historical-floor |
| `gate_raw_score` | REAL | confidence_gate | yes | pre-historical-floor; null if hard-escalator-driven |
| `gate_escalators` | JSON | confidence_gate | yes | list of fired hard-escalator kinds |
| `gate_mode` | TEXT | confidence_gate | yes | `"shadow"` or `"live"` |
| `gate_shadow_outcome` | TEXT | confidence_gate | yes | what the gate WOULD have decided in shadow mode (alongside the chunk-4 outcome that was actually used) |

`gate_outcome` and `gate_shadow_outcome` decouple "what the system did"
from "what the gate thought". In phase 1 they differ by design (the
gate is shadow). In phase 2+ they are equal except when the kill-switch
trips, in which case `gate_outcome` reflects the chunk-4 fallback and
`gate_shadow_outcome` reflects the (overruled) confidence gate.

### 7.2 New `metrics_events.jsonl` event kinds

The chunk 6d JSONL pattern is extended with:

| kind | when |
|---|---|
| `gate_shadow_decision` | every phase 1 decision: records what the gate would have done |
| `gate_live_decision` | every phase 2+ decision |
| `gate_hard_escalator_fired` | every time a hard escalator fires; one event per escalator |
| `gate_phase_advanced` | when the operator runs `cards-runner stats ramp advance` |
| `gate_phase_recommendation` | when the calibration loop recommends a phase change |
| `gate_killswitch_tripped` | when measured regression rate crosses the kill threshold |
| `gate_killswitch_cleared` | when the operator manually clears the kill switch |
| `verifier_risk_factor_emitted` | per risk factor the verifier emits |

Each event includes the full `inputs` dict from the `GateDecision`
where applicable so the decision is reproducible offline.

**Design decision: events are written even in phase 1 shadow mode.**
The whole point of phase 1 is to gather data on what the gate WOULD
decide so we can validate before flipping the switch. Skipping the
event-write in shadow mode would defeat the phase.

### 7.3 Calibration query

A new read API on the ledger:

```python
from cards_runner.metrics import calibration

c = calibration(
    bucket=("feature", 3),
    window_cards=100,
    by_band=True,            # decile-bucketing of confidence_score
)
# -> Calibration dataclass:
#    bands: [{lo, hi, n, regressions, regression_rate}, ...]
#    monotonic: bool          # does regression rate strictly decrease across bands?
#    overall_n: int
#    overall_regression_rate: float
```

A "calibrated" system has `monotonic == True` AND
`bands[top].regression_rate < cfg.regression_target_per_bucket`.
Section 9 uses this output to gate phase advancement.

---

## 8. The calibration loop

### 8.1 What "regression" means

The ledger spec section 9.1 defines it for the auto-merge trust signal
and that definition is reused verbatim. A merged card is "regressed"
if any of:

- A follow-up bugfix card cites it via `regresses:` within
  `cfg.regression_window_days` (default 14).
- A manual revert touched its merge commit.
- A `change_request:` traces back to it (sibling card amendment
  caused by this card's contract).

The confidence-gate spec does NOT re-define regression. Same source of
truth, same semantics. The only difference: where the ledger spec
slices regression rate by `(work_type, tier)`, the confidence-gate
calibration *also* slices by confidence_score band.

### 8.2 The calibration plot

For each `(work_type, tier)` bucket, plot:

```
band               n     regressions   rate
[0.95, 1.00]      40              0    0.0%
[0.90, 0.95)      35              1    2.9%
[0.85, 0.90)      28              2    7.1%
[0.80, 0.85)      19              3   15.8%
[0.75, 0.80)       9              3   33.3%
[ ... , 0.75)      ...
```

A *calibrated* system has monotonically increasing regression rate as
confidence band decreases. A *miscalibrated* system has bands where
"higher confidence" cards regress MORE than "lower confidence" cards.
That's a signal the soft-signal weights or the historical-floor
formula is wrong; do not advance phase until it is fixed.

The CLI `cards-runner stats calibration --work-type feature --tier 3`
prints this table.

### 8.3 What the loop does

The calibration loop runs (a) every time a card transitions to
terminal state with `gate_outcome` set, and (b) on a manual
`cards-runner stats calibration --recalibrate`. For each affected
bucket:

1. Recompute `bucket_regression_rate` over the rolling window.
2. Recompute per-band regression rates.
3. Test monotonicity.
4. Emit a `gate_phase_recommendation` event if the bucket is ready
   for phase advancement (or ready for tightening).
5. Update the `metric_estimates` cache row.

The loop never advances the phase on its own; it surfaces
recommendations and waits for the operator (section 9.3).

---

## 9. Conservative ramp

### 9.1 Phase 1: shadow mode (day 1, default)

- `confidence_gate_mode: "shadow"`.
- Chunk-4 tier routing is the authority.
- Confidence-gate runs and records decisions. No behavior change.
- All inputs, decisions, escalators, and outcomes write to the
  ledger.
- Duration: until the operator chooses to advance, with
  recommendation gates in 9.3.

This is non-negotiable as the default. Drew has not seen the gate run
yet; the gate has not seen Drew's projects yet; the soft-signal weights
are starting guesses. Day 1 is for *measurement*, not for *action*.

### 9.2 Phase 2: narrow live band

When the operator advances:
- `confidence_gate_mode: "live"`.
- `confidence_auto_threshold: 0.95`.
- `confidence_sibling_threshold: 0.85`.
- Full hard-escalator list active.
- Per-project `large_diff_threshold: 300` (tighter than chunk-4
  default; widens in phase 3).

Expectation: a majority of cards still route to sibling_review or
human_review; only the cleanest auto-merge. The band is intentionally
narrow so the early kill-switch evidence (if it trips) is on a small
sample.

### 9.3 Phase advancement gates

`cards-runner stats ramp advance` checks (per bucket, but reports
runner-wide):

| Phase | Required before advancing |
|---|---|
| 1 -> 2 | Per most-active `(work_type, tier)` buckets: n >= 30 shadow decisions; calibration monotonic. Recommendation event also reports the top-band shadow-decision rate so the operator can see whether the gate is actually finding auto-merge candidates -- a 0% rate is "safe" but useless and worth investigating before advancing. |
| 2 -> 3 | Per any bucket that has it: n >= 100 live decisions; rolling 30-day auto-merge regression rate at top band < 0.05; calibration still monotonic; no kill-switch event in the last 14 days. |
| 3 -> 4 | Per any bucket: n >= 300 live decisions; same calibration constraints; AND the linear-formula -> fitted-logistic-regression migration has landed and validated against held-out data. |

**Design decision: gates are per-bucket but advancement is
runner-wide.** A bucket that has not reached the per-bucket gate keeps
its phase 2 (or phase 1) thresholds even after the runner advances --
the runner stores `phase_per_bucket` in `metric_estimates`. This lets
high-volume buckets (where calibration data is dense) widen while
low-volume buckets stay conservative.

**Design decision: phase advancement is operator-explicit.** Mirrors
the ledger spec section 9.3 on the trust signal: the runner does NOT
autonomously demote / promote. It emits `gate_phase_recommendation`
events; the operator runs `cards-runner stats ramp advance --bucket
feature:3 --confirm` to apply.

### 9.4 Kill switch

If, in live mode, the rolling 30-day regression rate at the top
auto-band exceeds `2 * cfg.regression_target_per_bucket`, the
confidence-gate trips its kill switch:

1. Emit `gate_killswitch_tripped` event with the offending bucket and
   the trailing regression rate.
2. Add `regression_rate_alarm_active` to the hard-escalator list for
   that bucket (section 3.3).
3. Effectively: every card in that bucket routes to `human_review`
   until the operator clears the kill switch.
4. Calibration data continues to be collected; the bucket can recover
   and the operator can re-clear.

**Design decision: bucket-scoped, not runner-wide.** A single bucket
with elevated regression rate should not freeze every other bucket.
The escalator is added per-bucket via the bucket history's
`alarm_active` flag.

**Design decision: kill-switch is automatic, phase advance is
manual.** Asymmetric on purpose. Tightening (escalating to human
review) on measured bad outcomes is conservative -- the operator can
investigate. Widening (auto-merging more) on good-looking data is
exactly the territory where premature trust is dangerous, so it
requires the operator to look at the calibration plot and approve.

### 9.5 Phase persistence

Phase state lives in `metric_estimates` as a per-bucket column:

```
phase_per_bucket: INTEGER  -- 1, 2, 3, 4
```

Plus a runner-wide `default_phase` in `cards-runner` config for new
buckets. Migration from chunk-4 routing sets every existing bucket to
phase 1 on the first run after upgrade.

---

## 10. Per-project configuration

### 10.1 New `confidence_gate:` section in project config

```yaml
confidence_gate:
  mode: shadow                  # shadow | live; default shadow
  default_phase: 1              # for new buckets without history

  confidence_auto_threshold: 0.95
  confidence_sibling_threshold: 0.85
  large_diff_threshold: 300
  alpha_historical_floor: 2.0
  regression_target_per_bucket: 0.05
  regression_window_days: 14
  killswitch_multiplier: 2.0    # trip at 2x target rate

  sensitive_paths:
    - "src/auth/**"
    - "src/crypto/**"
    - "src/billing/**"
    - "src/migrations/**"
    - "**/secrets*"
    - "**/.env*"

  schema_migration_globs:
    - "migrations/**"
    - "**/schema*.sql"
    - "alembic/**"

  dependency_manifests:
    - "package.json"
    - "pyproject.toml"
    - "Cargo.toml"
    - "go.mod"

  hard_escalators_disabled: []   # operator can demote softs to inactive AFTER phase 3
  soft_signal_overrides: {}      # per-signal weight override; empty by default
```

### 10.2 Conservative defaults

The defaults above are intentionally cautious. A project that knows
its own risk profile may opt in to a smaller `sensitive_paths` set or
a higher `large_diff_threshold`, but the defaults assume a generic
codebase where the operator wants safety over speed on day 2.

### 10.3 Configuration hot-reload

Out of scope here. Same as chunk 4: project config is loaded at
daemon boot; changes take effect on next restart. Chunk 5+ adds
SIGHUP reload if it lands.

---

## 11. Data flow and module sketch

```
runner/src/cards_runner/
  daemon/
    confidence_gate.py        # NEW
    merge_gate.py             # MODIFIED: takes GateDecision as input
                              #          instead of computing band itself
    diff_stats.py             # NEW: git diff --numstat parser + helpers
  metrics/
    calibration.py            # NEW: per-bucket calibration plot + monotonicity
    ramp.py                   # NEW: phase advance / kill-switch state machine
  verifier/
    risk_factor.py            # NEW: RiskFactor dataclass + enum
    runner.py                 # MODIFIED: VerifierResult gains risk_factors
    subjective_evaluator.py   # MODIFIED: prompt + structured-output schema
```

Daemon `_dispatch_verifier` flow gains one step after verifier-pass
and before chunk-4 merge-gate apply:

```
def _dispatch_verifier(card):
    result = verify_card(card)
    if result.overall_status == "pass":
        diff_stats = DiffStats.from_worktree(card.worktree, card.branch,
                                              card.base_branch)
        sibling = self._sibling_result_for(card)   # None pre-chunk-5
        bucket_history = self.ledger.bucket_history(card.work_type, card.points)
        decision = self.confidence_gate.decide(card, result, sibling,
                                                diff_stats, bucket_history)
        self.ledger.write_gate_decision(card, decision)

        if self.confidence_gate.is_live():
            self.merge_gate.apply_with_decision(card, decision)
        else:
            chunk4_decision = self.merge_gate.decide_chunk4(card)
            self.merge_gate.apply_with_decision(card, chunk4_decision)
```

The chunk-4 `MergeGate.decide_gate` is preserved verbatim and renamed
`decide_chunk4` to make the fallback path explicit.

---

## 12. Open design questions

### 12.1 Where does `DiffStats` get the diff?

Two options:
- A. `git diff base_branch...card_branch --numstat` against the
  worktree. Works pre-PR. No `gh` dependency.
- B. `gh pr view --json files,additions,deletions` after `gh pr
  create` (chunk 4 already calls this for the merge). Adds a round
  trip to GitHub.

Recommendation: A. Diff is local; no need to round-trip GitHub for
a number the worktree knows. The chunk-4 merge gate already separates
the "decide gate" step from the "open PR" step; the gate decides
before the PR opens.

### 12.2 Does the gate run on cards whose verifier was skipped?

The v1.3 verifier MAY skip verification (deterministic phase) for
high-confidence cascade-clean runs. A skipped-verifier card has no
`risk_factors` because the subjective evaluator did not run.

Options:
- A. Gate runs anyway; `risk_factors` is empty; the soft signal just
  loses the risk-factor contribution.
- B. Gate refuses to auto-merge any card with a fully-skipped verifier
  (force at least `sibling_review`).
- C. Skipped-verifier cards keep the chunk-4 tier routing even in
  live mode.

Recommendation: B. A skipped verifier has explicitly NOT examined the
diff. Auto-merging on a no-information signal is the failure mode we
are trying to avoid. Sibling review is the minimum.

### 12.3 What about cards with subjective items that DID run but stayed at haiku?

A clean haiku pass is a real signal; the model said "I am confident"
and we have some evidence (cascade did not escalate). But haiku is the
weakest model; its over-confidence error is largest.

Options:
- A. Treat haiku-cleared identically to deterministic-only cards (soft
  signal +0.05 baseline).
- B. Cap auto-merge for haiku-only subjective cards at phase 3 or
  later, even if the soft score clears the threshold.
- C. Add a separate soft-signal contribution for "the model that cleared
  was Haiku vs Sonnet" with Haiku contributing less.

Recommendation: C (and the formula in section 3.7 already
distinguishes them). But the cap in B is also reasonable. Drew should
decide whether to layer them.

### 12.4 How should `diff_within_planner_declared_scope` work?

The soft signal `+0.05 if diff_within_planner_declared_scope` requires
a planner-declared scope. Today planners stamp expected file lists
inconsistently. Options:

- A. Make the planner write a `expected_files:` frontmatter list on
  every card; the soft-signal contributes only when the list is
  present and the diff fits inside it.
- B. Skip this signal until the planner reliably emits the field.
- C. Infer scope from the card's `acceptance_criteria` (the AC items
  that reference paths); use that inferred set.

Recommendation: A as a planner-side card-schema add, with a default
of "missing = signal contributes 0". C is tempting but fragile:
inferred scope from AC undercounts (an AC doesn't have to mention
every file it touches).

### 12.5 Should phase-advance ever be automatic?

The spec says no. But there is a case: a runner that has been in
phase 2 for a year with zero kill-switch events and steadily strong
calibration is being penalized for caution. The operator likely
*would* approve, but human approval is friction.

Options:
- A. Stay manual forever (the spec position).
- B. Allow per-bucket auto-advance after a longer dwell time (e.g. 90
  days at the same phase with zero kill events).
- C. Auto-emit a stronger recommendation event after long dwell but
  still require explicit confirm.

Recommendation: A for v1; revisit after the first year of real
ledger data.

### 12.6 What is the runner's behavior if the ledger is unreachable?

The confidence gate depends on the ledger for bucket history. If the
ledger SQLite store is locked / corrupt / missing:

Options:
- A. Fall back to chunk-4 tier routing (loud log).
- B. Force `human_review` for everything until ledger is available.
- C. Use the global cold-start prior from
  `runner/templates/metrics_priors.yaml` and proceed.

Recommendation: A. Tier routing is the known-good fallback; doing
"force human" indefinitely surprises operators with backlog buildup.
Emit a loud `ledger_unavailable` event.

### 12.7 Risk-factor enumeration cost

Asking the subjective evaluator to enumerate risk factors lengthens
its prompt and (slightly) lengthens its response. Concrete cost:
maybe +200 tokens in, +100 tokens out per subjective-evaluator call.
At Haiku pricing this is a rounding error. At Opus pricing for the
opus-tier escalations it's still under a cent.

Open: is there a project that does NOT want this overhead? Probably
not, but the project config could include
`subjective_risk_enumeration: false` for projects that explicitly opt
out. Recommendation: yes, include the knob; default true.

---

## 13. Sequencing if approved

Six independently-mergeable chunks. Chunks 5 (sibling reviewer) and
6a (auto_edit_ac) have already landed on main; this stack consumes
their outputs but does not require any change to them.

1. **Risk-factor schema and verifier shim.** `RiskFactor` dataclass
   in `verifier/risk_factor.py`; `VerifierResult.risk_factors` field
   (defaulted to empty for backward compat); subjective evaluator
   prompt and parser updated. No gate code yet; the field is just
   plumbed through and recorded as a no-op event in the existing
   `cascade_history` flow.

2. **Confidence-gate skeleton + shadow mode.** `ConfidenceGate`
   module with the full decision logic; `DiffStats` helper;
   `BucketHistory` reader from the ledger; ledger writes for
   `gate_shadow_decision` and `gate_hard_escalator_fired`.
   `confidence_gate_mode: shadow` is the default; live mode is
   permitted via config but the migration step does NOT flip it.

3. **Calibration loop and CLI surface.** `metrics/calibration.py`,
   `metrics/ramp.py`, `cards-runner stats calibration`,
   `cards-runner stats ramp`. Reads from the data chunk 2 wrote.

4. **Live-mode wiring + chunk-4 fallback.** `merge_gate.py` gains
   `apply_with_decision`; `_dispatch_verifier` flow updated to use
   the gate decision when live; chunk-4 `decide_chunk4` is the
   fallback. Kill-switch logic.

5. **`expected_files:` planner field and the scope soft signal.**
   Planner-side change to the /cards skill plus the gate
   reading the field. Optional and additive.

6. **Phase 3 prep: fitted-logistic-regression replacement.** Once
   any project has reached the n=300 per-bucket threshold, replace
   the linear formula with a fitted logistic regression. The
   inputs (soft signals) stay the same; only the combiner changes.
   Held-out validation against the linear formula required before
   cutover.

Steps 1-3 deliver phase 1 (shadow mode + measurement + calibration
visibility). Step 4 unlocks phase 2 live mode. Step 5 is additive
quality-of-signal. Step 6 is the long-term calibration upgrade.

PCS-quoting and roadmapping consumers do not depend on this stack;
they read the ledger directly. This spec adds *fields* to the ledger
but does not change any field they already consume.

---

## 14. Worked example

A tier-3 `feature` card finishes:
- Deterministic AC: 5 items, all pass first try.
- Subjective AC: 1 item, passed at haiku tier (confidence 0.91).
- Cascade history: empty (no runtime escalation).
- Rework cycles: 0.
- Diff: 180 lines added, 40 removed, all under `src/checkout/`.
- Risk factors: one medium (`incomplete_test_coverage`: "diff added
  `formatCheckoutTotal`; no matching test").
- Sibling reviewer: not wired up yet.
- Bucket history `(feature, 3)`: n=42, rolling regression rate
  0.024, monotonic calibration.
- Hard escalators: none fire.

Soft score:

```
0.50
+0.10  all deterministic first try
+0.05  subjective cleared at haiku
+0.00  no sonnet/opus cleared
-0.00  cascade history empty
-0.00  rework_cycles 0
-0.04  diff_total_lines 220 // 100 = 2 -> -0.04
+0.00  not test-only
+0.00  scope not declared (open question 12.4)
-0.07  one medium risk factor
+0.02  verifier confidence 0.91 -> (0.91 - 0.85)/0.15 * 0.05 = 0.02
-----
= 0.56

historical-floor:
0.56 * (1 - 2.0 * 0.024) = 0.56 * 0.952 = 0.533
```

Phase 2 thresholds: auto >= 0.95, sibling >= 0.85, else human.

`0.533 < 0.85` -> **human_review**.

Sensible outcome. The risk factor and the diff size held the score
down. A similar card without the risk factor and with the scope
signal would land at:

```
0.50 + 0.10 + 0.05 - 0.04 + 0.05 + 0.02 = 0.68
```

Still human_review at phase 2. To reach the auto band a card would
need a very clean profile: no medium risk factors, test-only or
small diff, sibling agreement signal, all positive bits firing.
That is the conservative shape Drew wants on day 1.

---

## 15. DESIGN READY FOR REVIEW

Done items:
- [x] Honest framing of the LLM-self-report problem (section 3.1).
- [x] Two-layer signal: hard escalators + soft formula (sections 3.2,
      3.3, 3.7).
- [x] Sibling-reviewer integration spec, including the no-sibling
      case (section 3.4).
- [x] Historical-floor adjustment using ledger data (section 3.5).
- [x] Risk-factor enumeration contract added to `VerifierResult`
      (section 3.6, 5.1, 5.2).
- [x] Gate decision logic, including shadow and live modes (section 4).
- [x] Integration with v1.3 verifier and chunk-4 MergeGate (sections
      5, 6).
- [x] Ledger schema extension and event kinds (section 7).
- [x] Calibration loop with the per-bucket monotonicity test
      (section 8).
- [x] Conservative ramp with shadow-first, per-bucket phase
      tracking, operator-explicit advancement, and automatic
      kill-switch (section 9).
- [x] Per-project configuration shape with conservative defaults
      (section 10).
- [x] Module-level data flow sketch (section 11).
- [x] Open questions enumerated for Drew (section 12).
- [x] Sequencing into six independently-mergeable chunks (section 13).
- [x] Worked example traced through the formula (section 14).

Awaiting:
- [ ] Drew's review and approval before any code lands.
- [ ] Resolution of the seven open questions in section 12.
- [ ] Confirmation on the default soft-signal weights in section 3.7
      (or a decision to defer their tuning to phase 1 calibration
      data).
- [ ] Confirmation on the default `sensitive_paths` set in section
      10.1 (project-specific).

When approved, the suggested branch sequence in section 13 turns
into a stack of implementation PRs on top of `main`. The chunk 5
sibling reviewer and chunk 6a auto_edit_ac stacks have already
landed; this spec consumes their outputs.
