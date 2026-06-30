"""Recalibrate `metric_estimates` from `card_metrics`.

Per spec section 8.4 the cache refreshes when:
- A card transitions to a terminal state (per-bucket incremental).
- The CLI `cards-runner stats recalibrate` is invoked (full re-run).
- The daemon boots (full re-run; cheap with small data).

This module is the orchestrator. It reads `card_metrics`, calls into
`estimator` for percentile + blend math, falls through to `priors` for
the cold-start case, and persists via `MetricsStore.upsert_estimate`.

Section 5.4 idempotency: the cumulative inputs come from card_metrics,
not from the previous estimate row, so re-running on the same data is
a no-op write.

Section 8.3 layered prior:
1. Per-(work_type, tier) -- the bucket's own empirical samples.
2. Per-tier -- aggregate samples across all work_types at this tier.
3. Global cold-start -- the YAML.

Layer 1 is the bucket sample set; layer 2 fires when the bucket sample
count is below `floor_calibration_n_floor`; layer 3 fires when the
tier-aggregate is also below floor.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..common.types import now_utc_iso
from .estimator import (
    blend,
    blend_scalar,
    percentiles_from_samples,
)
from .priors import Priors, PriorSet, layered_prior
from .store import CardMetricsRow, MetricEstimateRow, MetricsStore


# Below this sample count the layered prior falls through to the
# next-broader layer. Matches the spec section 8.3 default
# `floor_calibration_n_floor`.
DEFAULT_FLOOR_N: int = 10


@dataclass(frozen=True)
class RecalibrationResult:
    """One bucket's recalibration outcome.

    `prior_weight` is what the chunk-4 quote API surfaces as a
    confidence proxy. 1.0 means pure cold-start; 0.0 means pure
    empirical."""

    work_type: str
    tier: int
    n_samples: int
    prior_weight: float
    written: bool


def recalibrate_bucket(
    store: MetricsStore,
    priors: Priors,
    *,
    tenant_id: str,
    work_type: str,
    tier: int,
    floor_n: int = DEFAULT_FLOOR_N,
    at: str | None = None,
) -> RecalibrationResult:
    """Recompute and persist one bucket's metric estimate.

    Returns a `RecalibrationResult`. `written=False` when the bucket
    has no samples at all -- in that case the cold-start prior is the
    estimate and we still write it so the read API has something to
    return; `written=False` is reserved for true skips (e.g. the tier
    is not in the prior YAML, which the caller already validated)."""
    samples = store.fetch_bucket_samples(
        tenant_id=tenant_id, work_type=work_type, tier=tier,
    )
    usable = [s for s in samples if not s.incomplete_metrics]
    n_samples = len(usable)

    # Layer 1 input: this bucket's empirical samples.
    empirical_wall = percentiles_from_samples(
        [s.agent_wall_seconds for s in usable if s.agent_wall_seconds is not None]
    )
    empirical_tokens = percentiles_from_samples(
        [
            float(s.executor_tokens_total)
            for s in usable
            if s.executor_tokens_total is not None
        ]
    )
    empirical_review = percentiles_from_samples(
        [
            s.human_review_wall_seconds
            for s in usable
            if s.human_review_wall_seconds is not None
        ]
    )
    empirical_rework = _mean(
        [float(s.rework_cycles) for s in usable if s.rework_cycles is not None]
    )
    empirical_survival = _mean(
        [
            float(s.contract_survived)
            for s in usable
            if s.contract_survived is not None
        ]
    )

    # Layer 2: tier-aggregate when this bucket is below floor. The
    # aggregate excludes this bucket's own samples? No -- spec section
    # 8.3 reads the per-tier-aggregate as "across all work_types at
    # this tier" so we include this bucket. Including it shifts the
    # estimate slightly toward this bucket's data; that's the desired
    # behavior because the bucket is still our best local signal.
    if n_samples < floor_n:
        tier_aggregate_samples = _collect_tier_aggregate(
            store, tenant_id=tenant_id, tier=tier,
        )
        if len(tier_aggregate_samples) >= floor_n:
            tier_prior = _samples_to_prior_set(
                tier_aggregate_samples, fallback=layered_prior(tier, priors),
            )
        else:
            tier_prior = layered_prior(tier, priors)
    else:
        tier_prior = layered_prior(tier, priors)

    # Layer 3 = `layered_prior(tier, priors)` -- the global cold-start
    # YAML. Already wrapped into `tier_prior` above when layer 2 had
    # insufficient data.
    k = priors.k_for(work_type)
    blended_wall, weight = blend(
        empirical_wall, tier_prior.agent_wall_seconds,
        n_samples=n_samples, k=k,
    )
    blended_tokens, _ = blend(
        empirical_tokens, tier_prior.executor_tokens,
        n_samples=n_samples, k=k,
    )
    blended_review, _ = blend(
        empirical_review, tier_prior.human_review_wall_seconds,
        n_samples=n_samples, k=k,
    )
    blended_rework = blend_scalar(
        empirical_rework, tier_prior.rework_rate_mean,
        n_samples=n_samples, k=k,
    )
    blended_survival = blend_scalar(
        empirical_survival, tier_prior.contract_survival_rate,
        n_samples=n_samples, k=k,
    )

    estimate = MetricEstimateRow(
        tenant_id=tenant_id,
        work_type=work_type,
        tier=tier,
        n_samples=n_samples,
        agent_wall_seconds_p50=blended_wall.p50,
        agent_wall_seconds_p75=blended_wall.p75,
        agent_wall_seconds_p90=blended_wall.p90,
        executor_tokens_p50=int(round(blended_tokens.p50)),
        executor_tokens_p90=int(round(blended_tokens.p90)),
        human_review_wall_seconds_p50=blended_review.p50,
        human_review_wall_seconds_p90=blended_review.p90,
        rework_rate_mean=blended_rework,
        contract_survival_rate=blended_survival,
        last_calibrated_at=at or now_utc_iso(),
        prior_weight=weight,
    )
    store.upsert_estimate(estimate)
    return RecalibrationResult(
        work_type=work_type,
        tier=tier,
        n_samples=n_samples,
        prior_weight=weight,
        written=True,
    )


def recalibrate_all(
    store: MetricsStore,
    priors: Priors,
    *,
    tenant_id: str,
    floor_n: int = DEFAULT_FLOOR_N,
    at: str | None = None,
) -> list[RecalibrationResult]:
    """Recompute estimates for every populated `(work_type, tier)`
    bucket. Also recomputes any bucket that has a row in
    `metric_estimates` but no samples in `card_metrics` (the operator
    may have removed cards); those refresh against the cold-start
    prior so the surface stays honest.

    Commits once at the end so a partial failure leaves the previous
    cache intact rather than half-rewriting it.
    """
    buckets = set(store.list_buckets(tenant_id=tenant_id))
    # Include any bucket the cache knows about but card_metrics no
    # longer has samples for -- the cache row gets refreshed against
    # the cold-start prior (n_samples drops to 0; prior_weight -> 1.0).
    for existing in store.list_estimates(tenant_id=tenant_id):
        buckets.add((existing.work_type, existing.tier))
    timestamp = at or now_utc_iso()
    results: list[RecalibrationResult] = []
    for work_type, tier in sorted(buckets):
        if tier not in priors.tiers:
            # Operator omitted the tier from the priors file; skip
            # rather than fail. The doctor / CLI surfaces the omission
            # so the operator can choose to add the tier or accept
            # that the bucket stays uncalibrated.
            continue
        results.append(
            recalibrate_bucket(
                store, priors,
                tenant_id=tenant_id,
                work_type=work_type,
                tier=tier,
                floor_n=floor_n,
                at=timestamp,
            )
        )
    store.commit()
    return results


def _collect_tier_aggregate(
    store: MetricsStore, *, tenant_id: str, tier: int,
) -> list[CardMetricsRow]:
    """Tier-aggregate samples: every bucket at this tier."""
    aggregated: list[CardMetricsRow] = []
    for work_type, _t in store.list_buckets(tenant_id=tenant_id):
        if _t != tier:
            continue
        aggregated.extend(
            store.fetch_bucket_samples(
                tenant_id=tenant_id, work_type=work_type, tier=tier,
            )
        )
    return [s for s in aggregated if not s.incomplete_metrics]


def _samples_to_prior_set(
    samples: list[CardMetricsRow], *, fallback: PriorSet,
) -> PriorSet:
    """Build a `PriorSet` from a sample list (used for layer 2,
    tier-aggregate). Falls back to the supplied global prior on
    per-metric basis when the samples lack values for a metric."""
    wall = percentiles_from_samples(
        [s.agent_wall_seconds for s in samples if s.agent_wall_seconds is not None]
    )
    tokens = percentiles_from_samples(
        [
            float(s.executor_tokens_total)
            for s in samples
            if s.executor_tokens_total is not None
        ]
    )
    review = percentiles_from_samples(
        [
            s.human_review_wall_seconds
            for s in samples
            if s.human_review_wall_seconds is not None
        ]
    )
    rework = _mean(
        [float(s.rework_cycles) for s in samples if s.rework_cycles is not None]
    )
    survival = _mean(
        [
            float(s.contract_survived)
            for s in samples
            if s.contract_survived is not None
        ]
    )
    return PriorSet(
        agent_wall_seconds=wall if wall is not None else fallback.agent_wall_seconds,
        executor_tokens=(
            tokens if tokens is not None else fallback.executor_tokens
        ),
        human_review_wall_seconds=(
            review if review is not None else fallback.human_review_wall_seconds
        ),
        rework_rate_mean=(
            rework if rework is not None else fallback.rework_rate_mean
        ),
        contract_survival_rate=(
            survival if survival is not None else fallback.contract_survival_rate
        ),
    )


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


__all__ = [
    "DEFAULT_FLOOR_N",
    "RecalibrationResult",
    "recalibrate_all",
    "recalibrate_bucket",
]
