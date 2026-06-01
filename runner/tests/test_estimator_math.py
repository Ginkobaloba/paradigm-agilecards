"""Pure-math tests for `cards_runner.metrics.estimator`.

No store, no YAML, no I/O. Just the percentile and shrinkage
primitives.
"""
from __future__ import annotations

import pytest

from cards_runner.metrics.estimator import (
    PercentileSet,
    blend,
    blend_scalar,
    percentiles_from_samples,
)


def test_percentiles_empty_returns_none() -> None:
    assert percentiles_from_samples([]) is None


def test_percentiles_single_sample_collapses() -> None:
    result = percentiles_from_samples([42.0])
    assert result is not None
    assert result.p50 == 42.0
    assert result.p75 == 42.0
    assert result.p90 == 42.0


def test_percentiles_two_samples_interpolate() -> None:
    # numpy.percentile([10, 20], 50) == 15.0; 75 == 17.5; 90 == 19.0
    result = percentiles_from_samples([10.0, 20.0])
    assert result is not None
    assert result.p50 == pytest.approx(15.0)
    assert result.p75 == pytest.approx(17.5)
    assert result.p90 == pytest.approx(19.0)


def test_percentiles_sorted_independently_of_input_order() -> None:
    a = percentiles_from_samples([5.0, 1.0, 9.0, 3.0, 7.0])
    b = percentiles_from_samples([1.0, 3.0, 5.0, 7.0, 9.0])
    assert a == b


def test_percentiles_heavy_tail_p90_grabs_tail() -> None:
    """A handful of small values plus one huge outlier: P50 stays
    small, P90 pulls toward the outlier. Robust to heavy tails is the
    whole point per spec section 8.5."""
    samples = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 100.0]
    result = percentiles_from_samples(samples)
    assert result is not None
    assert result.p50 == pytest.approx(1.0)
    assert result.p75 == pytest.approx(1.0)
    # numpy.percentile([1,1,1,1,1,1,1,1,1,100], 90) == 10.9
    assert result.p90 == pytest.approx(10.9)


def test_blend_n_zero_returns_prior_with_full_weight() -> None:
    prior = PercentileSet(p50=10.0, p75=20.0, p90=30.0)
    blended, weight = blend(None, prior, n_samples=0, k=5)
    assert blended == prior
    assert weight == 1.0


def test_blend_n_equals_k_is_fifty_fifty() -> None:
    """k=5, n=5 -> w_emp = 5/10 = 0.5, w_prior = 5/10 = 0.5."""
    prior = PercentileSet(p50=10.0, p75=20.0, p90=30.0)
    empirical = PercentileSet(p50=20.0, p75=40.0, p90=60.0)
    blended, weight = blend(empirical, prior, n_samples=5, k=5)
    assert weight == pytest.approx(0.5)
    assert blended.p50 == pytest.approx(15.0)
    assert blended.p75 == pytest.approx(30.0)
    assert blended.p90 == pytest.approx(45.0)


def test_blend_n_much_larger_than_k_approaches_empirical() -> None:
    """n=50, k=5 -> w_emp = 50/55 ≈ 0.909; the blend is dominated by
    the empirical signal per spec section 8.2."""
    prior = PercentileSet(p50=10.0, p75=20.0, p90=30.0)
    empirical = PercentileSet(p50=100.0, p75=200.0, p90=300.0)
    blended, weight = blend(empirical, prior, n_samples=50, k=5)
    assert weight == pytest.approx(5 / 55)
    assert blended.p50 == pytest.approx(100.0 * 50 / 55 + 10.0 * 5 / 55)


def test_blend_rejects_negative_k() -> None:
    prior = PercentileSet(p50=10.0, p75=20.0, p90=30.0)
    empirical = PercentileSet(p50=20.0, p75=40.0, p90=60.0)
    with pytest.raises(ValueError):
        blend(empirical, prior, n_samples=5, k=-1)


def test_blend_scalar_mirrors_blend_for_scalars() -> None:
    """The orchestrator uses one formula for percentile metrics and a
    scalar variant for rates; the math must agree on the same n / k."""
    assert blend_scalar(0.8, 0.2, n_samples=5, k=5) == pytest.approx(0.5)
    assert blend_scalar(None, 0.2, n_samples=0, k=5) == 0.2


def test_blend_scalar_rejects_negative_k() -> None:
    with pytest.raises(ValueError):
        blend_scalar(0.8, 0.2, n_samples=5, k=-1)
