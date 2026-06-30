"""Worktree creation (skip-git mode).

The daemon supports `skip_worktree=True` for tests against a
non-git tmp dir; we exercise that path here. Real `git worktree
add` behavior is verified in chunk 2 against a real fixture repo
(since chunk 1's stub workers do not commit).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cards_runner.common.types import RuntimePaths
from cards_runner.daemon.worktree import (
    WorktreeCreateError, prepare_worktree, teardown_worktree,
)


def test_prepare_worktree_skip_git_creates_dir(
    paths: RuntimePaths, tmp_path: Path
) -> None:
    worktree = paths.runs / "abc-123" / "worktree"
    prepare_worktree(
        paths=paths,
        project_dir=tmp_path,
        branch_name="card/bTST-01",
        base_branch="main",
        worktree_path=worktree,
        skip_git=True,
    )
    assert worktree.is_dir()
    # Sentinel file documenting the stub branch / base.
    sentinel = worktree / ".cards-stub-worktree"
    assert sentinel.is_file()
    content = sentinel.read_text(encoding="utf-8")
    assert "branch=card/bTST-01" in content
    assert "base=main" in content


def test_prepare_worktree_real_git_path_fails_without_repo(
    paths: RuntimePaths, tmp_path: Path
) -> None:
    # `project_dir` is not a git repo; the real-mode path must raise.
    worktree = paths.runs / "xyz-456" / "worktree"
    with pytest.raises(WorktreeCreateError):
        prepare_worktree(
            paths=paths,
            project_dir=tmp_path,
            branch_name="card/whatever",
            base_branch="main",
            worktree_path=worktree,
            skip_git=False,
        )


def test_teardown_worktree_skip_git_removes_dir(
    paths: RuntimePaths, tmp_path: Path
) -> None:
    worktree = paths.runs / "tear-down" / "worktree"
    prepare_worktree(
        paths=paths,
        project_dir=tmp_path,
        branch_name="card/bTST-99",
        base_branch="main",
        worktree_path=worktree,
        skip_git=True,
    )
    assert worktree.exists()
    teardown_worktree(
        paths=paths,
        project_dir=tmp_path,
        worktree_path=worktree,
        skip_git=True,
    )
    assert not worktree.exists()
