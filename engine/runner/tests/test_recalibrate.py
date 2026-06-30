"""Tests for the recalibrate orchestrator.

End-to-end: card_metrics rows -> recalibrate -> metric_estimates rows.
Layered prior fallthrough and idempotency are the load-bearing
properties.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cards_runner.metrics import (
    load_priors,
    recalibrate_all,
    recalibrate_bucket,
)
from cards_runner.metrics.store import MetricsStore
from cards_runner.store.sqlite_store import SqliteRepository


def _seed_sample(
    conn: sqlite3.Connection,
    *,
    card_id: str,
    work_type: str = "feature",
    tier: int = 3,
    agent_wall_seconds: float | None = None,
    executor_tokens_total: int | None = None,
    human_review_wall_seconds: float | None = None,
    rework_cycles: int = 0,
    contract_survived: int = 1,
    incomplete_metrics: int = 0,
    tenant_id: str = "default",
) -> None:
    conn.execute(
        "INSERT INTO card_metrics (tenant_id, card_id, work_type, tier,"
        " agent_wall_seconds, executor_tokens_total,"
        " human_review_wall_seconds, rework_cycles, contract_survived,"
        " incomplete_metrics, regression_card_ids, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tenant_id, card_id, work_type, tier,
            agent_wall_seconds, executor_tokens_total,
            human_review_wall_seconds, rework_cycles, contract_survived,
            incomplete_metrics, "[]", "2026-05-29T18:00:00Z",
        ),
    )
    conn.commit()


def test_empty_bucket_writes_prior_with_full_weight(store_path: Path) -> None:
    """Cold start: no samples -> the cold-start prior lands as the
    estimate with `prior_weight=1.0` and `n_samples=0`."""
    repo = SqliteRepository.open(str(store_path))
    try:
        store = MetricsStore.from_repository(repo)
        priors = load_priors()
        result = recalibrate_bucket(
            store, priors,
            tenant_id="default", work_type="feature", tier=3,
            at="2026-05-29T18:00:00Z",
        )
        assert result.n_samples == 0
        assert result.prior_weight == 1.0
        assert result.written is True
        repo._conn.commit()
        landed = store.get_estimate(
            tenant_id="default", work_type="feature", tier=3,
        )
        assert landed is not None
        # The prior for tier 3 is 900 / 1800 / 3600 per metrics_priors.yaml.
        assert landed.agent_wall_seconds_p50 == 900.0
        assert landed.agent_wall_seconds_p75 == 1800.0
        assert landed.agent_wall_seconds_p90 == 3600.0
        assert landed.n_samples == 0
        assert landed.prior_weight == 1.0
        assert landed.last_calibrated_at == "2026-05-29T18:00:00Z"
    finally:
        repo.close()


def test_n_equals_k_blends_fifty_fifty(store_path: Path) -> None:
    """k=5 default. Insert 5 samples with constant wall=600.
    Empirical P50=600, prior P50=900 -> blended P50=750."""
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        for i in range(5):
            _seed_sample(
                conn, card_id=f"bF-{i}", agent_wall_seconds=600.0,
                executor_tokens_total=30_000,
                human_review_wall_seconds=600.0,
            )
        store = MetricsStore.from_repository(repo)
        priors = load_priors()
        recalibrate_bucket(
            store, priors,
            tenant_id="default", work_type="feature", tier=3,
        )
        conn.commit()
        landed = store.get_estimate(
            tenant_id="default", work_type="feature", tier=3,
        )
        assert landed is not None
        assert landed.n_samples == 5
        # 5/10 * 600 + 5/10 * 900 = 750
        assert landed.agent_wall_seconds_p50 == 750.0
        # prior_weight = k / (n + k) = 5/10 = 0.5
        assert landed.prior_weight == 0.5
    finally:
        repo.close()


def test_large_n_dominates_empirical(store_path: Path) -> None:
    """n=50, k=5 -> w_prior = 5/55 ≈ 0.091; result is mostly empirical."""
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        for i in range(50):
            _seed_sample(
                conn, card_id=f"bF-{i}", agent_wall_seconds=600.0,
            )
        store = MetricsStore.from_repository(repo)
        priors = load_priors()
        recalibrate_bucket(
            store, priors,
            tenant_id="default", work_type="feature", tier=3,
        )
        conn.commit()
        landed = store.get_estimate(
            tenant_id="default", work_type="feature", tier=3,
        )
        assert landed is not None
        assert landed.n_samples == 50
        # 50/55 * 600 + 5/55 * 900 = ~627.27
        assert landed.agent_wall_seconds_p50 == round(
            50 / 55 * 600 + 5 / 55 * 900, 4
        ) or abs(landed.agent_wall_seconds_p50 - (50 / 55 * 600 + 5 / 55 * 900)) < 0.01
        assert abs(landed.prior_weight - 5 / 55) < 0.001
    finally:
        repo.close()


def test_incomplete_metrics_rows_excluded(store_path: Path) -> None:
    """Spec section 12.2 / 8.2: incomplete-metrics rows must not enter
    the percentile computation."""
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        # Three "complete" rows at 600s wall...
        for i in range(3):
            _seed_sample(
                conn, card_id=f"bF-{i}", agent_wall_seconds=600.0,
                incomplete_metrics=0,
            )
        # ...plus an incomplete row at 6000s (should be ignored).
        _seed_sample(
            conn, card_id="bF-bad", agent_wall_seconds=6000.0,
            incomplete_metrics=1,
        )
        store = MetricsStore.from_repository(repo)
        priors = load_priors()
        result = recalibrate_bucket(
            store, priors,
            tenant_id="default", work_type="feature", tier=3,
        )
        conn.commit()
        assert result.n_samples == 3
        landed = store.get_estimate(
            tenant_id="default", work_type="feature", tier=3,
        )
        assert landed is not None
        # 3/8 * 600 + 5/8 * 900 = 787.5 (NOT pulled by the 6000 outlier)
        assert abs(landed.agent_wall_seconds_p50 - 787.5) < 0.01
    finally:
        repo.close()


def test_idempotent_under_replay(store_path: Path) -> None:
    """Spec section 5.4: re-running recalibrate on unchanged data
    produces the same row. The cache table never accumulates state."""
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        for i in range(7):
            _seed_sample(
                conn, card_id=f"bF-{i}", agent_wall_seconds=600.0 + i * 10,
            )
        store = MetricsStore.from_repository(repo)
        priors = load_priors()
        recalibrate_bucket(
            store, priors,
            tenant_id="default", work_type="feature", tier=3,
            at="2026-05-29T18:00:00Z",
        )
        conn.commit()
        first = store.get_estimate(
            tenant_id="default", work_type="feature", tier=3,
        )
        recalibrate_bucket(
            store, priors,
            tenant_id="default", work_type="feature", tier=3,
            at="2026-05-29T18:00:00Z",
        )
        conn.commit()
        second = store.get_estimate(
            tenant_id="default", work_type="feature", tier=3,
        )
        assert first == second
        # And only one row exists for the bucket.
        all_rows = store.list_estimates(tenant_id="default")
        assert len(all_rows) == 1
    finally:
        repo.close()


def test_recalibrate_all_covers_every_populated_bucket(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        _seed_sample(conn, card_id="bF-1", work_type="feature", tier=3,
                     agent_wall_seconds=500.0)
        _seed_sample(conn, card_id="bR-1", work_type="refactor", tier=2,
                     agent_wall_seconds=200.0)
        _seed_sample(conn, card_id="bB-1", work_type="bugfix", tier=4,
                     agent_wall_seconds=2000.0)
        store = MetricsStore.from_repository(repo)
        priors = load_priors()
        results = recalibrate_all(store, priors, tenant_id="default")
        names = {(r.work_type, r.tier) for r in results}
        assert names == {("feature", 3), ("refactor", 2), ("bugfix", 4)}
        assert all(r.written for r in results)
        assert len(store.list_estimates(tenant_id="default")) == 3
    finally:
        repo.close()


def test_recalibrate_all_refreshes_stale_cache_bucket(store_path: Path) -> None:
    """If `metric_estimates` carries a bucket whose `card_metrics`
    samples have been removed, the recalibration loop must refresh
    that bucket against the cold-start prior. Otherwise the cache
    would surface stale empirical data forever."""
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        # Seed an estimate directly: looks like a previous calibration.
        from cards_runner.metrics.store import MetricEstimateRow
        store = MetricsStore.from_repository(repo)
        store.upsert_estimate(MetricEstimateRow(
            tenant_id="default", work_type="feature", tier=3,
            n_samples=42,
            agent_wall_seconds_p50=100.0,
            agent_wall_seconds_p75=200.0,
            agent_wall_seconds_p90=300.0,
            executor_tokens_p50=1000, executor_tokens_p90=5000,
            human_review_wall_seconds_p50=60.0,
            human_review_wall_seconds_p90=120.0,
            rework_rate_mean=0.5, contract_survival_rate=0.5,
            last_calibrated_at="2026-05-20T00:00:00Z",
            prior_weight=0.1,
        ))
        conn.commit()
        priors = load_priors()
        results = recalibrate_all(store, priors, tenant_id="default")
        # The bucket has no card_metrics rows, so the result must
        # report n_samples=0 and prior_weight=1.0.
        assert len(results) == 1
        assert results[0].n_samples == 0
        assert results[0].prior_weight == 1.0
        landed = store.get_estimate(
            tenant_id="default", work_type="feature", tier=3,
        )
        assert landed is not None
        assert landed.n_samples == 0
        # The cold-start prior values for tier 3 lands.
        assert landed.agent_wall_seconds_p50 == 900.0
    finally:
        repo.close()


def test_recalibrate_all_empty_returns_no_results(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        store = MetricsStore.from_repository(repo)
        priors = load_priors()
        results = recalibrate_all(store, priors, tenant_id="default")
        assert results == []
        assert store.list_estimates(tenant_id="default") == []
    finally:
        repo.close()


def test_tier_aggregate_falls_through_when_bucket_below_floor(
    store_path: Path,
) -> None:
    """Section 8.3 layer 2: when the bucket has < floor_n samples but
    the tier-aggregate has >= floor_n, the prior is built from the
    tier aggregate, not the cold-start YAML."""
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        # 1 sample in the target bucket (`feature`, 3) at wall=600...
        _seed_sample(
            conn, card_id="bF-1", work_type="feature", tier=3,
            agent_wall_seconds=600.0,
        )
        # ...and 12 samples at tier 3 in OTHER work_types at wall=1200.
        for i in range(12):
            _seed_sample(
                conn, card_id=f"bR-{i}", work_type="refactor", tier=3,
                agent_wall_seconds=1200.0,
            )
        store = MetricsStore.from_repository(repo)
        priors = load_priors()
        recalibrate_bucket(
            store, priors,
            tenant_id="default", work_type="feature", tier=3,
            floor_n=10,
        )
        conn.commit()
        landed = store.get_estimate(
            tenant_id="default", work_type="feature", tier=3,
        )
        assert landed is not None
        # k=5, n=1, w_emp = 1/6, w_prior = 5/6.
        # Tier-aggregate P50 is the median of [600] + 12 * [1200] = 1200.
        # Blended P50 = 1/6 * 600 + 5/6 * 1200 = 1100.
        assert abs(landed.agent_wall_seconds_p50 - 1100.0) < 0.01
    finally:
        repo.close()
