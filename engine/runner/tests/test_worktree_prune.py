"""Tests for the chunk-5 `git worktree prune` sweep."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from cards_runner.common.types import DaemonConfig
from cards_runner.daemon import worktree as worktree_mod
from cards_runner.daemon.daemon import Daemon
from cards_runner.daemon.worktree import prune_git_worktrees
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text


def test_prune_returns_none_for_non_git_dir(tmp_path: Path) -> None:
    assert prune_git_worktrees(project_dir=tmp_path) is None


def test_prune_runs_git_when_repo_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    record: list[dict[str, Any]] = []

    class _Proc:
        returncode = 0
        stdout = "Removing worktrees/abc"
        stderr = ""

    def _fake(args: list[str], **kw: Any) -> _Proc:
        record.append({"args": args, **kw})
        return _Proc()

    monkeypatch.setattr(worktree_mod.subprocess, "run", _fake)
    out = prune_git_worktrees(project_dir=tmp_path)
    assert out is not None
    # PowerShell wrapping on Windows means the actual git invocation is
    # inside a "& 'git' 'worktree' 'prune' '-v'" command; on Linux the
    # args list is direct. Cover both shapes.
    cmd_text = " ".join(record[0]["args"])
    assert "worktree" in cmd_text and "prune" in cmd_text


def test_prune_with_expire_passes_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    record: list[dict[str, Any]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        worktree_mod.subprocess, "run",
        lambda args, **kw: (record.append({"args": args, **kw}), _Proc())[1],
    )
    prune_git_worktrees(project_dir=tmp_path, expire_after="1.week.ago")
    text = " ".join(record[0]["args"])
    assert "--expire" in text and "1.week.ago" in text


def test_prune_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()

    def _fake(args: list[str], **kw: Any) -> None:
        raise subprocess.CalledProcessError(
            returncode=1, cmd=args, stderr="boom"
        )

    monkeypatch.setattr(worktree_mod.subprocess, "run", _fake)
    out = prune_git_worktrees(project_dir=tmp_path)
    assert out is None


def test_prune_timeout_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()

    def _fake(args: list[str], **kw: Any) -> None:
        raise subprocess.TimeoutExpired(args, timeout=1.0)

    monkeypatch.setattr(worktree_mod.subprocess, "run", _fake)
    out = prune_git_worktrees(project_dir=tmp_path)
    assert out is None


def test_daemon_prune_skipped_when_disabled(
    repo: SqliteRepository, todo_root: Path, store_spec: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        skip_worktree=True,
        worktree_prune_enabled=False,
        verifier_enabled=False,
    )
    called: list[int] = []
    monkeypatch.setattr(
        "cards_runner.daemon.daemon.prune_git_worktrees",
        lambda **kw: called.append(1) or None,
    )
    daemon = Daemon(cfg, repo=repo)
    daemon._boot()
    daemon._tick()
    assert called == []


def test_daemon_prune_skipped_when_skip_worktree_true(
    repo: SqliteRepository, todo_root: Path, store_spec: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with the flag on, a skip-worktree daemon (which never
    creates real worktrees) must not invoke the prune."""
    cfg = DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        skip_worktree=True,
        worktree_prune_enabled=True,
        verifier_enabled=False,
    )
    called: list[int] = []
    monkeypatch.setattr(
        "cards_runner.daemon.daemon.prune_git_worktrees",
        lambda **kw: called.append(1) or None,
    )
    daemon = Daemon(cfg, repo=repo)
    daemon._boot()
    daemon._tick()
    assert called == []


def test_daemon_prune_runs_when_enabled_and_real_worktree(
    repo: SqliteRepository, todo_root: Path, store_spec: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()
    record = card_text_to_record(_card_text("bWP-01", points=2))
    record.project = str(project_dir)
    repo.create_card(record)

    cfg = DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        skip_worktree=False,  # let the prune actually fire.
        worktree_prune_enabled=True,
        worktree_prune_interval_sec=1,
        verifier_enabled=False,
    )
    invocations: list[Path] = []
    monkeypatch.setattr(
        "cards_runner.daemon.daemon.prune_git_worktrees",
        lambda project_dir: invocations.append(project_dir) or None,
    )
    daemon = Daemon(cfg, repo=repo)
    daemon._boot()
    daemon._maybe_prune_git_worktrees()
    assert len(invocations) == 1
    assert invocations[0].resolve() == project_dir.resolve()


def test_daemon_prune_rate_limited(
    repo: SqliteRepository, todo_root: Path, store_spec: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()
    record = card_text_to_record(_card_text("bWP-02", points=2))
    record.project = str(project_dir)
    repo.create_card(record)

    cfg = DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        skip_worktree=False,
        worktree_prune_enabled=True,
        worktree_prune_interval_sec=3600,
        verifier_enabled=False,
    )
    invocations: list[Path] = []
    monkeypatch.setattr(
        "cards_runner.daemon.daemon.prune_git_worktrees",
        lambda project_dir: invocations.append(project_dir) or None,
    )
    daemon = Daemon(cfg, repo=repo)
    daemon._boot()
    daemon._maybe_prune_git_worktrees()
    daemon._maybe_prune_git_worktrees()  # within interval: no-op.
    assert len(invocations) == 1
    # Reset the timer and try again.
    daemon._last_prune_at = time.monotonic() - 7200
    daemon._maybe_prune_git_worktrees()
    assert len(invocations) == 2
