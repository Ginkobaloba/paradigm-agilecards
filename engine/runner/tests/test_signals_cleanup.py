"""Tests for `cards_runner.daemon.signals_cleanup`."""
from __future__ import annotations

import os
import time
from pathlib import Path

from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.signals_cleanup import (
    split_decisions,
    sweep_reviewer_markers,
)
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text


def _insert_card(repo: SqliteRepository, card_id: str, status: str) -> None:
    record = card_text_to_record(_card_text(card_id, points=2))
    record.status = status
    repo.create_card(record)


def _make_marker(paths: RuntimePaths, subdir: str, card_id: str, *, age_seconds: float) -> Path:
    p = paths.signals / subdir / f"{card_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}", encoding="utf-8")
    if age_seconds > 0:
        past = time.time() - age_seconds
        os.utime(p, (past, past))
    return p


def _cfg(tmp_path: Path, *, ttl_hours: int = 72) -> DaemonConfig:
    return DaemonConfig(
        todo_root=tmp_path,
        reviewer_marker_ttl_hours=ttl_hours,
    )


def test_removes_old_marker_for_terminal_card(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_card(repo, "bSC-01", CardStatus.DONE.value)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    marker = _make_marker(paths, "sibling_reviews", "bSC-01", age_seconds=200 * 3600)
    decisions = sweep_reviewer_markers(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
    )
    assert any(d.action == "removed" for d in decisions)
    assert not marker.exists()


def test_keeps_recent_marker_even_for_terminal_card(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_card(repo, "bSC-02", CardStatus.DONE.value)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    marker = _make_marker(paths, "sibling_reviews", "bSC-02", age_seconds=10 * 3600)
    decisions = sweep_reviewer_markers(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
    )
    assert all(d.action == "kept_recent" for d in decisions)
    assert marker.exists()


def test_keeps_old_marker_for_active_card(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_card(repo, "bSC-03", CardStatus.ACTIVE.value)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    marker = _make_marker(paths, "sibling_reviews", "bSC-03", age_seconds=200 * 3600)
    decisions = sweep_reviewer_markers(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
    )
    assert any(d.action == "kept_active" for d in decisions)
    assert marker.exists()


def test_removes_orphan_marker_with_no_card(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    marker = _make_marker(paths, "amendment_reviews", "bGHOST", age_seconds=200 * 3600)
    decisions = sweep_reviewer_markers(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
    )
    assert any(d.action == "removed_orphan" for d in decisions)
    assert not marker.exists()


def test_ttl_zero_disables_sweep(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_card(repo, "bSC-05", CardStatus.DONE.value)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    marker = _make_marker(paths, "sibling_reviews", "bSC-05", age_seconds=200 * 3600)
    decisions = sweep_reviewer_markers(
        repo=repo, cfg=_cfg(tmp_path, ttl_hours=0), paths=paths,
    )
    assert decisions == []
    assert marker.exists()


def test_sweeps_both_subdirs(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_card(repo, "bSC-06", CardStatus.DONE.value)
    _insert_card(repo, "bSC-07", CardStatus.BLOCKED.value)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    m1 = _make_marker(paths, "sibling_reviews", "bSC-06", age_seconds=200 * 3600)
    m2 = _make_marker(paths, "amendment_reviews", "bSC-07", age_seconds=200 * 3600)
    decisions = sweep_reviewer_markers(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
    )
    removed = [d for d in decisions if d.action == "removed"]
    assert len(removed) == 2
    assert not m1.exists()
    assert not m2.exists()


def test_missing_signals_dir_is_noop(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    paths = RuntimePaths.from_root(tmp_path)
    # Don't ensure dirs.
    decisions = sweep_reviewer_markers(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
    )
    assert decisions == []


def test_split_decisions_groups_by_action(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_card(repo, "bSC-08", CardStatus.DONE.value)
    _insert_card(repo, "bSC-09", CardStatus.ACTIVE.value)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    _make_marker(paths, "sibling_reviews", "bSC-08", age_seconds=200 * 3600)
    _make_marker(paths, "sibling_reviews", "bSC-09", age_seconds=200 * 3600)
    _make_marker(paths, "sibling_reviews", "bSC-10", age_seconds=200 * 3600)
    decisions = sweep_reviewer_markers(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
    )
    groups = split_decisions(decisions)
    assert "removed" in groups
    assert "kept_active" in groups
    assert "removed_orphan" in groups
