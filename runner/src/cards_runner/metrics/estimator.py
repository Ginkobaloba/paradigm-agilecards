"""Pure-math primitives for the throughput-metrics estimator.

No I/O. No store. No prior loading. Two responsibilities:

1. Compute percentiles from a sample list (`percentiles_from_samples`).
2. Blend empirical percentiles with a prior using the section 8.2
   shrinkage formula (`blend`).

The orchestrator (`recalibrate.py`) is the I/O boundary; this module
is what it calls per bucket.

Percentile algorithm: linear interpolation (numpy's default mode).
This is what the spec's section 8.5 wants -- robust to heavy tails,
identical to what an analyst would compute manually. Deliberately not
the nearest-rank variant because that introduces a discretization
artifact at small n that the blend cannot smooth over.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PercentileSet:
    """P50 / P75 / P90 for one metric.

    Stored as floats; the caller coerces to int when the storage
    column is integer-typed (executor_tokens_p50, executor_tokens_p90).
    """

    p50: float
    p75: float
    p90: float


@dataclass(frozen=True)
class Estimate:
    """The public per-bucket estimate.

    Mirrors the section 11.1 Python surface and the section 3.3
    `metric_estimates` schema. Every field corresponds 1:1 with a
    column on `metric_estimates` so the orchestrator can persist this
    struct directly.

    `prior_weight` and `n_samples` together drive the chunk-4 quote
    API's confidence rating: low n or high prior weight -> low
    confidence, regardless of how clean the percentile output looks.
    """

    work_type: str
    tier: int
    n_samples: int
    agent_wall_seconds: PercentileSet
    executor_tokens: PercentileSet
    human_review_wall_seconds: PercentileSet
    rework_rate_mean: float
    contract_survival_rate: float
    prior_weight: float


def percentiles_from_samples(samples: list[float]) -> PercentileSet | None:
    """Return P50/P75/P90 over `samples`, or None if the list is empty.

    Linear interpolation between bracketing samples after sorting
    (numpy's default percentile algorithm; we re-implement it here
    rather than pull numpy in because the runner is otherwise
    numpy-free).
    """
    n = len(samples)
    if n == 0:
        return None
    if n == 1:
        v = float(samples[0])
        return PercentileSet(p50=v, p75=v, p90=v)
    ordered = sorted(float(s) for s in samples)
    return PercentileSet(
        p50=_linear_percentile(ordered, 0.50),
        p75=_linear_percentile(ordered, 0.75),
        p90=_linear_percentile(ordered, 0.90),
    )


def _linear_percentile(ordered: list[float], q: float) -> float:
    """Linear-interpolated q-th percentile of an already-sorted list.

    Same algorithm as numpy's `percentile(..., interpolation='linear')`
    (renamed `method='linear'` in newer numpy). The fractional rank
    `q * (n - 1)` splits between two neighboring samples; we interpolate
    on that fraction.
    """
    n = len(ordered)
    if n == 1:
        return ordered[0]
    rank = q * (n - 1)
    lower = int(rank)
    upper = min(lower + 1, n - 1)
    fraction = rank - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def blend(
    empirical: PercentileSet | None,
    prior: PercentileSet,
    *,
    n_samples: int,
    k: int,
) -> tuple[PercentileSet, float]:
    """Bayesian-with-shrinkage blend of empirical observations and a prior.

    Per spec section 8.2:
        weight_empirical = n / (n + k)
        weight_prior     = k / (n + k)
        estimate         = w_emp * empirical + w_prior * prior

    At n=0 the result is the prior with weight 1.0; at n=k it's the
    50/50 blend; at n=10*k the prior contributes ~9%.

    `k` is the shrinkage constant (default 5 per spec section 8.2).
    A bigger k pulls the estimate toward the prior longer; the spec
    permits per-work_type tuning via the priors YAML's
    `shrinkage_k_overrides`.

    Returns `(blended PercentileSet, prior_weight)`. The prior weight
    is what the chunk-4 quote API surfaces as a confidence proxy.
    """
    if empirical is None or n_samples <= 0:
        return prior, 1.0
    if k < 0:
        raise ValueError(f"shrinkage k must be non-negative, got {k!r}")
    w_emp = n_samples / (n_samples + k)
    w_prior = k / (n_samples + k)
    blended = PercentileSet(
        p50=w_emp * empirical.p50 + w_prior * prior.p50,
        p75=w_emp * empirical.p75 + w_prior * prior.p75,
        p90=w_emp * empirical.p90 + w_prior * prior.p90,
    )
    return blended, w_prior


def blend_scalar(
    empirical: float | None,
    prior: float,
    *,
    n_samples: int,
    k: int,
) -> float:
    """Same shrinkage formula, applied to a scalar (rework rate,
    survival rate). Symmetric with `blend` so the orchestrator uses
    one formula for every metric."""
    if empirical is None or n_samples <= 0:
        return prior
    if k < 0:
        raise ValueError(f"shrinkage k must be non-negative, got {k!r}")
    w_emp = n_samples / (n_samples + k)
    w_prior = k / (n_samples + k)
    return w_emp * empirical + w_prior * prior


__all__ = [
    "Estimate",
    "PercentileSet",
    "blend",
    "blend_scalar",
    "percentiles_from_samples",
]
