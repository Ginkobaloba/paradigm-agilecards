"""Tests for the chunk-5 `pr_url` promoted column.

Covers:

- The column is present on a freshly-initialized SQLite database.
- `initialize_schema()` is idempotent and adds the column to an existing
  database that pre-dates chunk 5.
- The merge gate writes pr_url into the row via the daemon's outcome
  application path.
- `parse_card_text` reads `pr_url` from frontmatter and promotes it.
- Round-trip projection keeps pr_url across read/write cycles.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cards_runner.common.types import ClaimedCard, DaemonConfig, RuntimePaths
from cards_runner.daemon.daemon import Daemon
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.schema import ddl_statements
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import FakeGhRunner, _card_text


def test_pr_url_column_present_on_fresh_db(store_path: Path) -> None:
    SqliteRepository.open(str(store_path)).close()
    conn = sqlite3.connect(str(store_path))
    cur = conn.execute("PRAGMA table_info(cards)")
    names = {row[1] for row in cur.fetchall()}
    conn.close()
    assert "pr_url" in names


def test_initialize_schema_adds_pr_url_to_legacy_db(store_path: Path) -> None:
    """Simulate a chunk-4-era DB by running the CREATE TABLE without pr_url
    and then reopening it through the chunk-5 schema initializer."""
    legacy_create = (
        "CREATE TABLE IF NOT EXISTS cards ("
        "tenant_id TEXT NOT NULL, card_id TEXT NOT NULL, status TEXT NOT NULL,"
        " title TEXT, project TEXT, batch TEXT, points INTEGER, stakes TEXT,"
        " difficulty TEXT, claimed_by TEXT, attempt_trace_id TEXT,"
        " model_used TEXT, created TEXT, started_at TEXT, finished_at TEXT,"
        " last_heartbeat TEXT, merge_status TEXT, verified_at TEXT,"
        " verified_by TEXT, estimated_tokens INTEGER, actual_tokens INTEGER,"
        " story_hash TEXT, trace_id TEXT,"
        " frontmatter_extra TEXT NOT NULL DEFAULT '{}',"
        " frontmatter_raw TEXT NOT NULL DEFAULT '',"
        " body_md TEXT NOT NULL DEFAULT '',"
        " updated_at TEXT,"
        " PRIMARY KEY (tenant_id, card_id))"
    )
    conn = sqlite3.connect(str(store_path))
    conn.execute(legacy_create)
    # Insert one row directly to make sure ALTER doesn't drop existing data.
    conn.execute(
        "INSERT INTO cards (tenant_id, card_id, status, frontmatter_extra,"
        " frontmatter_raw, body_md) VALUES ('default', 'legacy-1', 'backlog',"
        " '{}', '', '')"
    )
    conn.commit()
    cur = conn.execute("PRAGMA table_info(cards)")
    names_before = {row[1] for row in cur.fetchall()}
    assert "pr_url" not in names_before
    conn.close()

    repo = SqliteRepository.open(str(store_path))
    try:
        # The new column is now present.
        check = sqlite3.connect(str(store_path))
        cur = check.execute("PRAGMA table_info(cards)")
        names_after = {row[1] for row in cur.fetchall()}
        check.close()
        assert "pr_url" in names_after
        # The legacy row survived and is readable through the repo.
        record = repo.get_card("legacy-1")
        assert record is not None
        assert record.pr_url is None
    finally:
        repo.close()


def test_initialize_schema_is_idempotent(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    repo.initialize_schema()
    repo.initialize_schema()  # should not raise.
    repo.close()


def test_projection_round_trip_keeps_pr_url(store_path: Path) -> None:
    text = _card_text("bPRU-01", points=2).replace(
        "merge_status: pending\n",
        "merge_status: pending\npr_url: https://github.com/x/y/pull/1\n",
    )
    record = card_text_to_record(text)
    assert record.pr_url == "https://github.com/x/y/pull/1"


def test_merge_gate_writes_pr_url_to_card_row(
    repo: SqliteRepository,
    todo_root: Path,
    store_spec: str,
) -> None:
    """End-to-end: a passing card with the merge gate enabled lands a
    pr_url on the row, queryable through `get_card`."""
    cfg = DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        verifier_enabled=False,  # we skip the verifier for this slice.
        pr_gate_enabled=True,
        skip_worktree=True,
    )
    fake_gh = FakeGhRunner()
    daemon = Daemon(cfg, repo=repo, gh=fake_gh)
    daemon._boot()
    record = card_text_to_record(_card_text("bPRU-02", points=2))
    repo.create_card(record)
    claimed = repo.claim_card("bPRU-02", claimed_by="test")
    assert claimed is not None
    paths = RuntimePaths.from_root(todo_root)
    claim = ClaimedCard(
        card_id="bPRU-02",
        attempt_trace_id="attempt-1",
        trace_id=str(claimed.trace_id or "trace-1"),
        run_dir=paths.runs / "attempt-1",
        worktree_path=paths.runs / "attempt-1" / "worktree",
        card_file=paths.runs / "attempt-1" / "card.md",
    )
    # Drive the post-verifier transition directly.
    daemon._verifier_apply_pass(claim, result=None, skip_reason="test-skip")
    landed = repo.get_card("bPRU-02")
    assert landed is not None
    assert landed.status == CardStatus.DONE.value
    assert landed.pr_url == "https://github.com/x/y/pull/42"
    # And that the schema DDL we ship continues to include pr_url for
    # the sqlite dialect (regression guard against an accidental drop).
    ddl = " ".join(ddl_statements("sqlite"))
    assert "pr_url" in ddl


def test_query_cards_returns_pr_url(repo: SqliteRepository) -> None:
    record = card_text_to_record(
        _card_text("bPRU-03", points=2).replace(
            "merge_status: pending\n",
            "merge_status: pending\npr_url: https://github.com/x/y/pull/9\n",
        )
    )
    repo.create_card(record)
    fetched = repo.get_card("bPRU-03")
    assert fetched is not None
    assert fetched.pr_url == "https://github.com/x/y/pull/9"
    listed = repo.query_cards()
    assert any(c.pr_url == "https://github.com/x/y/pull/9" for c in listed)
