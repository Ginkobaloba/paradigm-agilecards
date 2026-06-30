"""Stub worker heartbeat propagation against a projected card file.

Drives the worker directly (rather than through the daemon spawner)
so the test stays Linux-friendly. The worker reads and writes the
per-run projected card file the daemon would have written into the
run dir; here the fixture claims a card through the store and
projects it itself.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from cards_runner.common.card_io import parse_card_file
from cards_runner.common.types import (
    HEARTBEAT_FILE,
    PROJECTED_CARD_NAME,
    RuntimePaths,
    parse_iso,
)
from cards_runner.daemon.worktree import prepare_worktree
from cards_runner.store.projection import project_card_file
from cards_runner.store.sqlite_store import SqliteRepository
from cards_runner.worker_stub.invoker import StubInvoker
from cards_runner.worker_stub.worker import run_worker


@pytest.fixture
def claimed(
    repo: SqliteRepository,
    card_factory: Any,
    paths: RuntimePaths,
    tmp_path: Path,
) -> tuple[Path, Path, str]:
    """Claim a card and project it into a per-run dir.

    Returns `(projected_card_file, worktree, attempt_trace_id)`.
    """
    card_factory("bTST-03-heartbeat")
    attempt = uuid.uuid4().hex
    record = repo.claim_card(
        "bTST-03-heartbeat", claimed_by="tester", attempt_trace_id=attempt
    )
    assert record is not None
    run_dir = paths.runs / attempt
    run_dir.mkdir(parents=True, exist_ok=True)
    worktree = run_dir / "worktree"
    prepare_worktree(
        paths=paths,
        project_dir=tmp_path,
        branch_name="card/bTST-03-heartbeat",
        base_branch="main",
        worktree_path=worktree,
        skip_git=True,
    )
    card_file = run_dir / PROJECTED_CARD_NAME
    project_card_file(record, card_file, verbatim=False)
    return card_file, worktree, attempt


def test_stub_worker_writes_completion_notes(
    claimed: tuple[Path, Path, str]
) -> None:
    card_file, worktree, attempt = claimed
    rc = run_worker(
        card_path=card_file,
        worktree=worktree,
        attempt_trace_id=attempt,
        trace_id="trace-test",
        heartbeat_interval_sec=0.1,
        invoker=StubInvoker(sleep_sec=0.5),
    )
    assert rc == 0
    text = card_file.read_text(encoding="utf-8")
    assert "## Completion notes" in text
    assert "Stub executor" in text
    snap = parse_card_file(card_file)
    assert snap.frontmatter["finished_at"] is not None
    assert snap.frontmatter["actual_tokens"] == 0


def test_heartbeat_file_is_written(
    claimed: tuple[Path, Path, str]
) -> None:
    card_file, worktree, attempt = claimed
    rc = run_worker(
        card_path=card_file,
        worktree=worktree,
        attempt_trace_id=attempt,
        trace_id="trace-test",
        heartbeat_interval_sec=0.1,
        invoker=StubInvoker(sleep_sec=0.6),
    )
    assert rc == 0
    assert (worktree / HEARTBEAT_FILE).is_file()


def test_card_frontmatter_heartbeat_advances(
    claimed: tuple[Path, Path, str]
) -> None:
    card_file, worktree, attempt = claimed
    initial = parse_card_file(card_file)
    started_hb = parse_iso(initial.frontmatter["last_heartbeat"])
    assert started_hb is not None

    completion: dict[str, int] = {}

    def go() -> None:
        completion["rc"] = run_worker(
            card_path=card_file,
            worktree=worktree,
            attempt_trace_id=attempt,
            trace_id="trace-test",
            heartbeat_interval_sec=0.2,
            invoker=StubInvoker(sleep_sec=1.5),
        )

    t = threading.Thread(target=go)
    t.start()
    # Wait long enough for at least two heartbeat cycles.
    time.sleep(0.7)
    mid = parse_card_file(card_file)
    mid_hb = parse_iso(mid.frontmatter["last_heartbeat"])
    assert mid_hb is not None
    assert mid_hb >= started_hb
    t.join()
    assert completion["rc"] == 0
