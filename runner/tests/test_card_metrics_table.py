"""Tests for the ledger-chunk-1 `card_metrics` table.

The table is schema-only this chunk. Chunk 2 wires the writer; here we
just confirm the table lands on a fresh init, carries every column the
spec lists, has the bucket index, and is idempotent under re-init.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cards_runner.store.schema import (
    CARD_METRICS_COLUMNS,
    EXPECTED_TABLES,
    ddl_statements,
)
from cards_runner.store.sqlite_store import SqliteRepository


def test_card_metrics_table_present_on_fresh_db(store_path: Path) -> None:
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='card_metrics'"
    )
    rows = cur.fetchall()
    conn.close()
    assert rows, "card_metrics table missing on fresh DB"


def test_card_metrics_columns_match_spec(store_path: Path) -> None:
    """Every column in `CARD_METRICS_COLUMNS` must exist on the live
    table. The spec section 3.1 lists ~24 fields; the test guards
    against any silent drift between the column tuple and the DDL."""
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute("PRAGMA table_info(card_metrics)")
    names = {row[1] for row in cur.fetchall()}
    conn.close()
    missing = set(CARD_METRICS_COLUMNS) - names
    assert not missing, f"card_metrics missing columns: {sorted(missing)}"


def test_card_metrics_primary_key_is_tenant_card(store_path: Path) -> None:
    """PK matches the spec's join semantics with `cards`: one metrics
    row per `(tenant_id, card_id)`."""
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute("PRAGMA table_info(card_metrics)")
    pk_cols = sorted((row[5], row[1]) for row in cur.fetchall() if row[5])
    conn.close()
    assert [name for _, name in pk_cols] == ["tenant_id", "card_id"]


def test_card_metrics_bucket_index_present(store_path: Path) -> None:
    """The estimator queries `(tenant_id, work_type, tier)` every
    recalibration; the index is what makes that cheap."""
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='card_metrics' AND name='idx_card_metrics_bucket'"
    )
    rows = cur.fetchall()
    conn.close()
    assert rows, "idx_card_metrics_bucket missing on fresh DB"


def test_card_metrics_in_expected_tables() -> None:
    assert "card_metrics" in EXPECTED_TABLES


def test_card_metrics_ddl_in_both_dialects() -> None:
    for dialect in ("sqlite", "mysql"):
        joined = " ".join(ddl_statements(dialect))
        assert "CREATE TABLE IF NOT EXISTS card_metrics" in joined, (
            f"{dialect} DDL missing card_metrics"
        )


def test_card_metrics_idempotent_init(store_path: Path) -> None:
    """A second open does not raise; CREATE TABLE IF NOT EXISTS does
    its job and the post-create migration loop is a no-op."""
    SqliteRepository.open(str(store_path)).close()
    SqliteRepository.open(str(store_path)).close()


def test_card_metrics_round_trip_insert_and_read(store_path: Path) -> None:
    """End-to-end: a directly-inserted metrics row reads back with the
    expected types. This is the chunk-2 writer's substrate so we
    confirm the columns accept the spec's value shapes today."""
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    conn.execute(
        "INSERT INTO card_metrics (tenant_id, card_id, work_type, tier,"
        " pin_required, agent_wall_seconds, agent_attempts,"
        " executor_tokens_total, executor_cost_usd, rework_cycles,"
        " regression_card_ids, contract_survived, incomplete_metrics,"
        " merge_gate, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "default", "bMET-01", "feature", 3,
            0, 720.5, 2,
            42_000, 0.21, 1,
            '["bMET-02"]', 1, 0,
            "sibling_review", "2026-05-29T18:00:00Z",
        ),
    )
    conn.commit()
    cur = conn.execute(
        "SELECT card_id, work_type, tier, agent_wall_seconds,"
        " executor_tokens_total, regression_card_ids, contract_survived"
        " FROM card_metrics WHERE card_id = 'bMET-01'"
    )
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "bMET-01"
    assert row[1] == "feature"
    assert row[2] == 3
    assert row[3] == 720.5
    assert row[4] == 42_000
    assert row[5] == '["bMET-02"]'
    assert row[6] == 1
