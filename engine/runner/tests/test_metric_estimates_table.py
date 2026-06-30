"""Tests for the ledger-chunk-1 `metric_estimates` table.

Like `card_metrics`, this is schema-only this chunk. The estimator
writer lands in ledger chunk 3.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cards_runner.store.schema import (
    EXPECTED_TABLES,
    METRIC_ESTIMATES_COLUMNS,
    ddl_statements,
)
from cards_runner.store.sqlite_store import SqliteRepository


def test_metric_estimates_table_present_on_fresh_db(store_path: Path) -> None:
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='metric_estimates'"
    )
    rows = cur.fetchall()
    conn.close()
    assert rows, "metric_estimates table missing on fresh DB"


def test_metric_estimates_columns_match_spec(store_path: Path) -> None:
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute("PRAGMA table_info(metric_estimates)")
    names = {row[1] for row in cur.fetchall()}
    conn.close()
    missing = set(METRIC_ESTIMATES_COLUMNS) - names
    assert not missing, f"metric_estimates missing columns: {sorted(missing)}"


def test_metric_estimates_primary_key_is_bucket(store_path: Path) -> None:
    """The bucket is the slicing key per spec section 3.3. We add
    `tenant_id` in front to match the system-wide multi-tenant
    convention -- the spec showed `(work_type, tier)` only because the
    spec's section was scoped to one tenant."""
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute("PRAGMA table_info(metric_estimates)")
    pk_cols = sorted((row[5], row[1]) for row in cur.fetchall() if row[5])
    conn.close()
    assert [name for _, name in pk_cols] == ["tenant_id", "work_type", "tier"]


def test_metric_estimates_in_expected_tables() -> None:
    assert "metric_estimates" in EXPECTED_TABLES


def test_metric_estimates_ddl_in_both_dialects() -> None:
    for dialect in ("sqlite", "mysql"):
        joined = " ".join(ddl_statements(dialect))
        assert "CREATE TABLE IF NOT EXISTS metric_estimates" in joined, (
            f"{dialect} DDL missing metric_estimates"
        )


def test_metric_estimates_idempotent_init(store_path: Path) -> None:
    SqliteRepository.open(str(store_path)).close()
    SqliteRepository.open(str(store_path)).close()


def test_metric_estimates_round_trip_insert_and_read(store_path: Path) -> None:
    """The estimator (chunk 3) will INSERT-or-REPLACE on this table.
    Confirm a directly-inserted row carries the spec's value shapes."""
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    conn.execute(
        "INSERT INTO metric_estimates (tenant_id, work_type, tier,"
        " n_samples, agent_wall_seconds_p50, agent_wall_seconds_p75,"
        " agent_wall_seconds_p90, executor_tokens_p50, executor_tokens_p90,"
        " rework_rate_mean, contract_survival_rate, last_calibrated_at,"
        " prior_weight)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "default", "feature", 3,
            42, 480.0, 720.0, 1320.0,
            18_000, 55_000,
            0.21, 0.84,
            "2026-05-29T18:00:00Z", 0.32,
        ),
    )
    conn.commit()
    cur = conn.execute(
        "SELECT work_type, tier, n_samples, agent_wall_seconds_p50,"
        " contract_survival_rate, prior_weight"
        " FROM metric_estimates WHERE work_type = 'feature' AND tier = 3"
    )
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "feature"
    assert row[1] == 3
    assert row[2] == 42
    assert row[3] == 480.0
    assert row[4] == 0.84
    assert row[5] == 0.32


def test_metric_estimates_insert_or_replace_idempotent(store_path: Path) -> None:
    """The recalibration loop replaces the bucket row on every run.
    Confirm the PK lets us insert-or-replace without duplication."""
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    for n_samples in (10, 20, 30):
        conn.execute(
            "INSERT OR REPLACE INTO metric_estimates"
            " (tenant_id, work_type, tier, n_samples) VALUES (?, ?, ?, ?)",
            ("default", "feature", 3, n_samples),
        )
    conn.commit()
    cur = conn.execute(
        "SELECT n_samples FROM metric_estimates"
        " WHERE work_type = 'feature' AND tier = 3"
    )
    rows = cur.fetchall()
    conn.close()
    assert rows == [(30,)]
