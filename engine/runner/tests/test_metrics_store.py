"""Tests for `cards_runner.metrics.store.MetricsStore`."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cards_runner.metrics.store import (
    MetricEstimateRow,
    MetricsStore,
)
from cards_runner.store.sqlite_store import SqliteRepository


def _seed(conn: sqlite3.Connection, **fields) -> None:
    defaults = dict(
        tenant_id="default",
        card_id="bMET-X",
        work_type="feature",
        tier=3,
        pin_required=0,
        agent_wall_seconds=None,
        agent_attempts=1,
        executor_tokens_total=None,
        executor_cost_usd=None,
        verifier_tokens_total=None,
        reviewer_tokens_total=None,
        human_review_wall_seconds=None,
        rework_cycles=0,
        diff_lines_added=None,
        diff_lines_removed=None,
        merge_gate=None,
        merged_at=None,
        regression_card_ids="[]",
        contract_survived=1,
        incomplete_metrics=0,
        updated_at="2026-05-29T18:00:00Z",
    )
    defaults.update(fields)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    conn.execute(
        f"INSERT INTO card_metrics ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    conn.commit()


def test_from_repository_extracts_conn(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        store = MetricsStore.from_repository(repo)
        assert store.list_buckets(tenant_id="default") == []
    finally:
        repo.close()


def test_from_repository_rejects_repo_without_conn() -> None:
    class _Bare:
        pass

    with pytest.raises(TypeError, match="SQL connection"):
        MetricsStore.from_repository(_Bare())


def test_list_buckets_returns_distinct_pairs(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        _seed(conn, card_id="bF-1", work_type="feature", tier=3)
        _seed(conn, card_id="bF-2", work_type="feature", tier=3)
        _seed(conn, card_id="bF-3", work_type="feature", tier=4)
        _seed(conn, card_id="bR-1", work_type="refactor", tier=3)
        store = MetricsStore.from_repository(repo)
        assert store.list_buckets(tenant_id="default") == [
            ("feature", 3),
            ("feature", 4),
            ("refactor", 3),
        ]
    finally:
        repo.close()


def test_list_buckets_skips_null_work_type_or_tier(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        _seed(conn, card_id="bN-1", work_type=None, tier=3)
        _seed(conn, card_id="bN-2", work_type="feature", tier=None)
        _seed(conn, card_id="bV-1", work_type="feature", tier=3)
        store = MetricsStore.from_repository(repo)
        assert store.list_buckets(tenant_id="default") == [("feature", 3)]
    finally:
        repo.close()


def test_list_buckets_isolates_tenants(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        _seed(conn, card_id="bT-1", tenant_id="default", work_type="feature", tier=3)
        _seed(conn, card_id="bT-2", tenant_id="other", work_type="bugfix", tier=4)
        store = MetricsStore.from_repository(repo)
        assert store.list_buckets(tenant_id="default") == [("feature", 3)]
        assert store.list_buckets(tenant_id="other") == [("bugfix", 4)]
    finally:
        repo.close()


def test_fetch_bucket_samples_returns_rows(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        conn = repo._conn
        _seed(
            conn, card_id="bS-1", work_type="feature", tier=3,
            agent_wall_seconds=720.5, executor_tokens_total=42_000,
            human_review_wall_seconds=180.0, rework_cycles=1,
            contract_survived=1, incomplete_metrics=0,
        )
        store = MetricsStore.from_repository(repo)
        rows = store.fetch_bucket_samples(
            tenant_id="default", work_type="feature", tier=3,
        )
        assert len(rows) == 1
        sample = rows[0]
        assert sample.card_id == "bS-1"
        assert sample.agent_wall_seconds == 720.5
        assert sample.executor_tokens_total == 42_000
        assert sample.contract_survived == 1
        assert sample.incomplete_metrics == 0
    finally:
        repo.close()


def test_upsert_estimate_replaces_on_pk(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        store = MetricsStore.from_repository(repo)
        row_v1 = MetricEstimateRow(
            tenant_id="default", work_type="feature", tier=3,
            n_samples=5,
            agent_wall_seconds_p50=300.0,
            agent_wall_seconds_p75=400.0,
            agent_wall_seconds_p90=500.0,
            executor_tokens_p50=10000, executor_tokens_p90=30000,
            human_review_wall_seconds_p50=120.0,
            human_review_wall_seconds_p90=600.0,
            rework_rate_mean=0.2, contract_survival_rate=0.8,
            last_calibrated_at="2026-05-29T18:00:00Z",
            prior_weight=0.5,
        )
        store.upsert_estimate(row_v1)
        store.commit()
        # Replace the same PK with new values.
        row_v2 = MetricEstimateRow(
            tenant_id="default", work_type="feature", tier=3,
            n_samples=10,
            agent_wall_seconds_p50=600.0,
            agent_wall_seconds_p75=800.0,
            agent_wall_seconds_p90=1000.0,
            executor_tokens_p50=20000, executor_tokens_p90=60000,
            human_review_wall_seconds_p50=240.0,
            human_review_wall_seconds_p90=900.0,
            rework_rate_mean=0.1, contract_survival_rate=0.9,
            last_calibrated_at="2026-05-30T18:00:00Z",
            prior_weight=0.33,
        )
        store.upsert_estimate(row_v2)
        store.commit()
        all_rows = store.list_estimates(tenant_id="default")
        assert len(all_rows) == 1
        assert all_rows[0].n_samples == 10
        assert all_rows[0].agent_wall_seconds_p50 == 600.0
    finally:
        repo.close()


def test_get_estimate_returns_none_when_absent(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        store = MetricsStore.from_repository(repo)
        assert store.get_estimate(
            tenant_id="default", work_type="feature", tier=3,
        ) is None
    finally:
        repo.close()
