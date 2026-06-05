"""Confidence-driven merge gate -- decision engine (gate chunk 2 core).

`docs/design/confidence_driven_merge_gate.md`. The gate routes a
verifier-passed card to `auto` / `sibling_review` / `human_review` from a
composite confidence signal, NOT from static tier. Two layers (spec 3.2):

1. **Hard escalators** (spec 3.3): categorical "Drew looks at this"
   conditions that force `human_review` regardless of score.
2. **Soft signals** (spec 3.7): a documented linear formula producing a
   `raw_score`, adjusted by the bucket's historical regression rate
   (spec 3.5), then mapped to a band.

This module is the pure decision engine. It does NOT run git, read the
ledger, or touch the daemon: callers pass pre-extracted `GateInputs` and
a `BucketHistory`. The signal extraction (from the verifier result, the
worktree diff, and the ledger) and the shadow-mode recording are the
follow-on wiring (gate-2b); keeping them out here makes the scoring
exhaustively unit-testable and free of side effects.

`mode` defaults to `shadow`: even when wired, the gate records what it
WOULD decide without changing routing until an operator advances the ramp
(spec 9). Nothing here flips routing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..common.types import now_utc_iso
from ..verifier.risk_factor import RiskFactor, SEVERITY_HIGH, SEVERITY_LOW, SEVERITY_MEDIUM

# Outcomes (reuse the chunk-4 vocabulary; spec 2 "no new enum values").
OUTCOME_AUTO = "auto"
OUTCOME_SIBLING = "sibling_review"
OUTCOME_HUMAN = "human_review"

# Hard escalators that remain even at the most relaxed ramp phase
# (spec 3.3 "the list shrinks but never to empty"): these are policy, not
# trust calibration, so `hard_escalators_disabled` cannot remove them.
_POLICY_ESCALATORS: frozenset[str] = frozenset({
    "pin_required",
    "sensitive_path_touched",
    "schema_migration_in_diff",
    "regression_rate_alarm_active",
})


@dataclass(frozen=True)
class ConfidenceGateConfig:
    """Per-project gate config (spec 10.1). Conservative defaults."""

    mode: str = "shadow"  # "shadow" | "live"
    confidence_auto_threshold: float = 0.95
    confidence_sibling_threshold: float = 0.85
    large_diff_threshold: int = 300
    alpha_historical_floor: float = 2.0
    regression_target_per_bucket: float = 0.05
    sensitive_paths: tuple[str, ...] = (
        "src/auth/**", "src/crypto/**", "src/billing/**",
        "src/migrations/**", "**/secrets*", "**/.env*",
    )
    schema_migration_globs: tuple[str, ...] = (
        "migrations/**", "**/schema*.sql", "alembic/**",
    )
    dependency_manifests: tuple[str, ...] = (
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    )
    hard_escalators_disabled: tuple[str, ...] = ()


@dataclass(frozen=True)
class BucketHistory:
    """The `(work_type, tier)` bucket's recent track record (spec 3.5)."""

    regression_rate: float = 0.0
    n_samples: int = 0
    alarm_active: bool = False


@dataclass(frozen=True)
class GateInputs:
    """Pre-extracted signals the engine scores. Built by the daemon
    wiring (gate-2b) from the verifier result, the worktree diff, the
    sibling marker, and the card."""

    work_type: str | None
    tier: int | None
    pin_required: bool = False
    # Verifier-path quality.
    all_deterministic_first_try: bool = False
    subjective_cleared_tier: str | None = None  # haiku|sonnet|opus|None
    cascade_climbs: int = 0
    rework_cycles: int = 0
    verifier_confidence: float = 1.0
    verifier_incomplete_metrics: bool = False
    change_request_unresolved: bool = False
    # Sibling agreement.
    sibling_decision: str | None = None  # approve|request_changes|comment|None
    # Diff signals.
    diff_total_lines: int = 0
    diff_is_test_only: bool = False
    diff_within_declared_scope: bool = False
    sensitive_path_touched: bool = False
    schema_migration_in_diff: bool = False
    new_external_dependency: bool = False
    # Risk factors (spec 3.6).
    risk_factors: tuple[RiskFactor, ...] = ()


@dataclass(frozen=True)
class GateDecision:
    """The gate's structured decision (spec 6.2)."""

    outcome: str
    confidence_score: float
    raw_score: float | None
    escalators: tuple[str, ...]
    reason: str  # "hard_escalator" | "confidence_band"
    bucket: tuple[str | None, int | None]
    mode: str
    at: str
    inputs: dict[str, Any] = field(default_factory=dict)


class ConfidenceGate:
    """The decision engine. Construct with a config; call `decide`."""

    def __init__(self, config: ConfidenceGateConfig | None = None) -> None:
        self.config = config or ConfidenceGateConfig()

    def is_live(self) -> bool:
        """True when the runner should ROUTE on this gate's decision.
        Default config is shadow, so this is False until an operator
        opts a project into live mode."""
        return self.config.mode == "live"

    def decide(
        self, inputs: GateInputs, bucket_history: BucketHistory | None = None
    ) -> GateDecision:
        history = bucket_history or BucketHistory()
        escalators = self._hard_escalators(inputs, history)
        raw = self._raw_score(inputs)
        score = self._apply_floor(raw, history)
        bucket = (inputs.work_type, inputs.tier)
        audit = self._audit(inputs, history, raw, score)

        if escalators:
            return GateDecision(
                outcome=OUTCOME_HUMAN,
                confidence_score=score,
                raw_score=raw,
                escalators=tuple(escalators),
                reason="hard_escalator",
                bucket=bucket,
                mode=self.config.mode,
                at=now_utc_iso(),
                inputs=audit,
            )

        if score >= self.config.confidence_auto_threshold:
            outcome = OUTCOME_AUTO
        elif score >= self.config.confidence_sibling_threshold:
            outcome = OUTCOME_SIBLING
        else:
            outcome = OUTCOME_HUMAN
        return GateDecision(
            outcome=outcome,
            confidence_score=score,
            raw_score=raw,
            escalators=(),
            reason="confidence_band",
            bucket=bucket,
            mode=self.config.mode,
            at=now_utc_iso(),
            inputs=audit,
        )

    # ---- internals ---------------------------------------------------

    def _hard_escalators(
        self, inputs: GateInputs, history: BucketHistory
    ) -> list[str]:
        fired: list[str] = []
        if inputs.pin_required:
            fired.append("pin_required")
        if inputs.subjective_cleared_tier == "opus":
            fired.append("subjective_cascade_opus_used")
        if inputs.sensitive_path_touched:
            fired.append("sensitive_path_touched")
        if inputs.schema_migration_in_diff:
            fired.append("schema_migration_in_diff")
        if inputs.new_external_dependency:
            fired.append("new_external_dependency")
        if inputs.sibling_decision == "request_changes":
            fired.append("sibling_disagreement")
        if inputs.diff_total_lines > self.config.large_diff_threshold:
            fired.append("large_diff")
        if inputs.change_request_unresolved:
            fired.append("executor_change_request_unresolved")
        if inputs.verifier_incomplete_metrics:
            fired.append("verifier_incomplete_metrics")
        if any(rf.severity == SEVERITY_HIGH for rf in inputs.risk_factors):
            fired.append("risk_factor_high_severity")
        if history.alarm_active:
            fired.append("regression_rate_alarm_active")
        # An operator may disable soft escalators (spec 3.3) but never the
        # policy set.
        disabled = set(self.config.hard_escalators_disabled) - _POLICY_ESCALATORS
        return [e for e in fired if e not in disabled]

    def _raw_score(self, inputs: GateInputs) -> float:
        """The spec 3.7 linear formula. Starts uncertain at 0.50."""
        s = 0.50
        if inputs.all_deterministic_first_try:
            s += 0.10
        if inputs.subjective_cleared_tier == "haiku":
            s += 0.05
        elif inputs.subjective_cleared_tier == "sonnet":
            s += 0.02
        s -= 0.05 * inputs.cascade_climbs
        s -= 0.05 * inputs.rework_cycles
        if inputs.sibling_decision == "approve":
            s += 0.20
        s -= min(0.20, 0.02 * (inputs.diff_total_lines // 100))
        if inputs.diff_is_test_only:
            s += 0.10
        if inputs.diff_within_declared_scope:
            s += 0.05
        medium = sum(
            1 for rf in inputs.risk_factors if rf.severity == SEVERITY_MEDIUM
        )
        low = sum(
            1 for rf in inputs.risk_factors if rf.severity == SEVERITY_LOW
        )
        s -= min(0.25, 0.07 * medium)
        s -= min(0.10, 0.02 * low)
        if inputs.verifier_confidence > 0.85:
            s += min(0.05, 0.05 * (inputs.verifier_confidence - 0.85) / 0.15)
        return max(0.0, min(1.0, s))

    def _apply_floor(self, raw: float, history: BucketHistory) -> float:
        """Multiplicative historical-floor adjustment (spec 3.5)."""
        adjusted = raw * (1.0 - self.config.alpha_historical_floor
                          * history.regression_rate)
        return max(0.0, min(1.0, adjusted))

    def _audit(
        self, inputs: GateInputs, history: BucketHistory,
        raw: float, score: float,
    ) -> dict[str, Any]:
        return {
            "work_type": inputs.work_type,
            "tier": inputs.tier,
            "raw_score": round(raw, 4),
            "confidence_score": round(score, 4),
            "bucket_regression_rate": round(history.regression_rate, 4),
            "bucket_n_samples": history.n_samples,
            "all_deterministic_first_try": inputs.all_deterministic_first_try,
            "subjective_cleared_tier": inputs.subjective_cleared_tier,
            "cascade_climbs": inputs.cascade_climbs,
            "rework_cycles": inputs.rework_cycles,
            "sibling_decision": inputs.sibling_decision,
            "diff_total_lines": inputs.diff_total_lines,
            "diff_is_test_only": inputs.diff_is_test_only,
            "num_medium_risk": sum(
                1 for rf in inputs.risk_factors
                if rf.severity == SEVERITY_MEDIUM
            ),
            "num_high_risk": sum(
                1 for rf in inputs.risk_factors
                if rf.severity == SEVERITY_HIGH
            ),
            "verifier_confidence": inputs.verifier_confidence,
        }


def read_bucket_history(
    store: Any,
    *,
    tenant_id: str,
    work_type: str,
    tier: int,
    config: ConfidenceGateConfig | None = None,
) -> BucketHistory:
    """Build a `BucketHistory` from the ledger's `card_metrics` rows.

    `store` is a `metrics.MetricsStore`. The regression rate is the
    fraction of the bucket's cards with a non-empty `regression_card_ids`.
    The kill-switch alarm trips when the rate exceeds
    `killswitch_multiplier`-x the target (spec 9.4); here that threshold
    is `regression_target_per_bucket * 2.0` as a conservative default.
    Empty bucket -> a neutral zero-rate history."""
    cfg = config or ConfidenceGateConfig()
    n, regressed = store.bucket_regression(
        tenant_id=tenant_id, work_type=work_type, tier=tier
    )
    rate = (regressed / n) if n > 0 else 0.0
    alarm = rate > (cfg.regression_target_per_bucket * 2.0)
    return BucketHistory(regression_rate=rate, n_samples=n, alarm_active=alarm)


__all__ = [
    "BucketHistory",
    "ConfidenceGate",
    "ConfidenceGateConfig",
    "GateDecision",
    "GateInputs",
    "OUTCOME_AUTO",
    "OUTCOME_HUMAN",
    "OUTCOME_SIBLING",
    "read_bucket_history",
]
