"""Tests for the ledger-chunk-1 `work_type` promoted column.

The chunk-5 `pr_url` precedent is the model: the column lands as a new
`ADDED_COLUMNS` entry, the CREATE TABLE carries it on fresh databases,
and the projection round trip preserves it.

Covers:

- The column is present on a freshly-initialized SQLite database.
- `initialize_schema()` is idempotent and adds the column to a database
  whose CREATE TABLE predates the ledger.
- The column is indexed (work_type is a slicing key for the estimator).
- `parse_card_text` reads `work_type` from frontmatter and promotes it.
- Round-trip projection keeps the value across read/write cycles.
- Legacy cards without the field load with `work_type=None` (the
  "incomplete metrics" backfill case).
- The canonical work_type enum exposes the spec's nine values.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cards_runner.common.types import (
    CANONICAL_WORK_TYPES,
    WORK_TYPE_BUGFIX,
    WORK_TYPE_FEATURE,
    WORK_TYPE_UNKNOWN,
    is_canonical_work_type,
)
from cards_runner.store.projection import card_text_to_record, render_card_text
from cards_runner.store.schema import ADDED_COLUMNS, ddl_statements
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text


def test_work_type_column_present_on_fresh_db(store_path: Path) -> None:
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute("PRAGMA table_info(cards)")
    names = {row[1] for row in cur.fetchall()}
    conn.close()
    assert "work_type" in names


def test_work_type_index_present_on_fresh_db(store_path: Path) -> None:
    """The index makes `(work_type, tier)` bucket queries cheap. The
    estimator queries this slice every recalibration tick, so an index
    is a non-trivial part of the schema commitment."""
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='cards' AND name='idx_cards_work_type'"
    )
    rows = cur.fetchall()
    conn.close()
    assert rows, "idx_cards_work_type missing on fresh DB"


def test_initialize_schema_adds_work_type_to_legacy_db(store_path: Path) -> None:
    """Simulate a chunk-5-era DB (has pr_url but not work_type) and
    confirm the chunk-1 initializer adds the column without losing data."""
    legacy_create = (
        "CREATE TABLE IF NOT EXISTS cards ("
        "tenant_id TEXT NOT NULL, card_id TEXT NOT NULL, status TEXT NOT NULL,"
        " title TEXT, project TEXT, batch TEXT, points INTEGER, stakes TEXT,"
        " difficulty TEXT, claimed_by TEXT, attempt_trace_id TEXT,"
        " model_used TEXT, created TEXT, started_at TEXT, finished_at TEXT,"
        " last_heartbeat TEXT, merge_status TEXT, verified_at TEXT,"
        " verified_by TEXT, estimated_tokens INTEGER, actual_tokens INTEGER,"
        " story_hash TEXT, trace_id TEXT, pr_url TEXT,"
        " frontmatter_extra TEXT NOT NULL DEFAULT '{}',"
        " frontmatter_raw TEXT NOT NULL DEFAULT '',"
        " body_md TEXT NOT NULL DEFAULT '',"
        " updated_at TEXT,"
        " PRIMARY KEY (tenant_id, card_id))"
    )
    conn = sqlite3.connect(str(store_path))
    conn.execute(legacy_create)
    conn.execute(
        "INSERT INTO cards (tenant_id, card_id, status, frontmatter_extra,"
        " frontmatter_raw, body_md) VALUES ('default', 'legacy-wt', 'backlog',"
        " '{}', '', '')"
    )
    conn.commit()
    cur = conn.execute("PRAGMA table_info(cards)")
    names_before = {row[1] for row in cur.fetchall()}
    assert "work_type" not in names_before
    conn.close()

    repo = SqliteRepository.open(str(store_path))
    try:
        check = sqlite3.connect(str(store_path))
        cur = check.execute("PRAGMA table_info(cards)")
        names_after = {row[1] for row in cur.fetchall()}
        check.close()
        assert "work_type" in names_after
        # Legacy row survives the migration and is readable through the
        # repo with a NULL work_type.
        record = repo.get_card("legacy-wt")
        assert record is not None
        assert record.work_type is None
    finally:
        repo.close()


def test_added_columns_entry_for_work_type() -> None:
    """The chunk-1 column lands via `ADDED_COLUMNS`, not via a hand-rolled
    ALTER, so doctor's pending/applied report covers it for free."""
    entries = {(table, column) for table, column, *_ in ADDED_COLUMNS}
    assert ("cards", "work_type") in entries


def test_ddl_contains_work_type_column_both_dialects() -> None:
    """Defensive: a future schema edit that removes the column from the
    CREATE TABLE would only be caught at integration time without this
    grep-style guard."""
    for dialect in ("sqlite", "mysql"):
        joined = " ".join(ddl_statements(dialect))
        assert "work_type" in joined, f"{dialect} CREATE missing work_type"


def test_initialize_schema_is_idempotent(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    repo.initialize_schema()
    repo.initialize_schema()  # must not raise.
    repo.close()


def test_projection_round_trip_keeps_work_type(store_path: Path) -> None:
    text = _card_text("bWT-01", points=2).replace(
        "merge_status: pending\n",
        "merge_status: pending\nwork_type: feature\n",
    )
    record = card_text_to_record(text)
    assert record.work_type == WORK_TYPE_FEATURE
    # Re-projecting the card lands work_type in the canonical position.
    projected = render_card_text(record, verbatim=False)
    assert "work_type: feature" in projected


def test_card_without_work_type_loads_as_none(store_path: Path) -> None:
    """Pre-ledger backfill case: the field is missing, the record
    surfaces None, the estimator's writer (chunk 2) flags
    `incomplete_metrics`."""
    text = _card_text("bWT-02", points=2)
    record = card_text_to_record(text)
    assert record.work_type is None


def test_projection_rejects_noncanonical_work_type(store_path: Path) -> None:
    """Spec section 4.1: the runner validates work_type on projection and
    rejects an unknown enum value with a clear error. A planner typo must
    fail loudly rather than silently fragmenting the estimator buckets."""
    import pytest

    from cards_runner.store.projection import ProjectionError

    text = _card_text("bWT-05", points=2).replace(
        "merge_status: pending\n",
        "merge_status: pending\nwork_type: feaure\n",  # typo on purpose.
    )
    with pytest.raises(ProjectionError) as excinfo:
        card_text_to_record(text)
    message = str(excinfo.value)
    assert "feaure" in message
    assert "work_type" in message


def test_projection_accepts_unknown_work_type(store_path: Path) -> None:
    """`unknown` is a canonical value (the backfill escape valve), so an
    explicit `work_type: unknown` projects without error even though new
    planner-authored cards are not supposed to use it."""
    text = _card_text("bWT-06", points=2).replace(
        "merge_status: pending\n",
        "merge_status: pending\nwork_type: unknown\n",
    )
    record = card_text_to_record(text)
    assert record.work_type == WORK_TYPE_UNKNOWN


def test_query_cards_returns_work_type(repo: SqliteRepository) -> None:
    text = _card_text("bWT-03", points=2).replace(
        "merge_status: pending\n",
        "merge_status: pending\nwork_type: bugfix\n",
    )
    record = card_text_to_record(text)
    repo.create_card(record)
    fetched = repo.get_card("bWT-03")
    assert fetched is not None
    assert fetched.work_type == WORK_TYPE_BUGFIX
    listed = repo.query_cards()
    assert any(c.work_type == WORK_TYPE_BUGFIX for c in listed)


def test_update_card_fields_promotes_work_type(repo: SqliteRepository) -> None:
    """The runner (chunk 2) writes work_type through update_card_fields;
    confirm the field is promoted to its typed column, not stuck in
    `frontmatter_extra`."""
    text = _card_text("bWT-04", points=2)
    record = card_text_to_record(text)
    repo.create_card(record)
    repo.update_card_fields("bWT-04", {"work_type": WORK_TYPE_FEATURE})
    fetched = repo.get_card("bWT-04")
    assert fetched is not None
    assert fetched.work_type == WORK_TYPE_FEATURE
    assert "work_type" not in fetched.frontmatter_extra


def test_canonical_work_types_match_spec() -> None:
    """The spec section 4 lists nine canonical types verbatim. Any drift
    between the spec table and the enum here is a contract violation."""
    expected = {
        "feature",
        "refactor",
        "bugfix",
        "infrastructure",
        "docs",
        "spike",
        "contract",
        "migration",
        "unknown",
    }
    assert set(CANONICAL_WORK_TYPES) == expected


def test_is_canonical_work_type_predicate() -> None:
    assert is_canonical_work_type(WORK_TYPE_FEATURE)
    assert is_canonical_work_type(WORK_TYPE_UNKNOWN)
    assert not is_canonical_work_type(None)
    assert not is_canonical_work_type("")
    assert not is_canonical_work_type("Feature")  # case-sensitive on purpose.
    assert not is_canonical_work_type("typo")
