"""Worktree creation against a REAL git repo.

This file exists because its absence hid a P0.

`test_worktree_creation.py` covers `skip_git=True` and one real-mode case
that asserts failure *before* git is ever invoked (no repo present). Its
docstring promised that "real `git worktree add` behavior is verified in
chunk 2 against a real fixture repo". That test was never written. No test
in the suite ran `git init`. So the entire real-git path -- `_ensure_branch`,
`git worktree add`, `_verify_worktree` -- was unexercised, and the suite was
green while the daemon could not prepare a single worktree on Windows:

- `_verify_worktree` compared `str(worktree_path)` (backslashes on Windows)
  against `git worktree list` output (git always reports POSIX separators),
  so its own post-condition could never pass on the development platform.
- The failed verification left the worktree registered and the branch pinned
  to it, so the next claim hit "branch already used by worktree", rolled
  back, and the card livelocked at poll cadence, never reaching the executor
  or the AC gate.

Both are regression-tested here. These tests shell out to real git on
purpose: mocking the thing that was broken is what let it survive.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cards_runner.common.types import RuntimePaths
from cards_runner.daemon import worktree as worktree_mod
from cards_runner.daemon.worktree import (
    WorktreeCreateError,
    _registered_worktrees,
    prepare_worktree,
)


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="real-git tests require a git binary"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture()
def real_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on `main`."""
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Cards Runner Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    return repo


def test_prepare_worktree_real_git_creates_and_verifies(
    paths: RuntimePaths, real_repo: Path
) -> None:
    """The happy path, against real git.

    Regression test for the Windows path-separator bug: `_verify_worktree`
    used a substring match of a Windows path against git's POSIX-style
    listing, so this raised `WorktreeCreateError` on every card, forever,
    on the platform the runner actually runs on.
    """
    worktree = paths.runs / "real-001" / "worktree"

    prepare_worktree(
        paths=paths,
        project_dir=real_repo,
        branch_name="card/b001-01-real",
        base_branch="main",
        worktree_path=worktree,
        skip_git=False,
    )

    assert worktree.is_dir()
    # The seed file came across, so this is a real checkout, not a mkdir.
    assert (worktree / "README.md").is_file()
    # And git agrees it is registered.
    assert worktree.resolve() in _registered_worktrees(real_repo)


def test_registered_worktrees_returns_resolved_paths(
    paths: RuntimePaths, real_repo: Path
) -> None:
    """`_registered_worktrees` returns real Paths, comparable without
    caring about separators. The main worktree is always registered."""
    registered = _registered_worktrees(real_repo)
    assert real_repo.resolve() in registered
    assert all(isinstance(p, Path) for p in registered)


def test_prepare_worktree_is_atomic_when_verification_fails(
    paths: RuntimePaths, real_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed verification must not leave the worktree registered.

    Regression test for the livelock. `git worktree add` succeeds and pins
    the branch; if verification then raises and we leave that registration
    behind, the card rolls back to `backlog`, gets re-claimed, and
    `git worktree add` fails with "branch already used by worktree" on
    every subsequent attempt -- forever.

    The git here is real; only the verification verdict is forced, because
    the property under test is "prepare_worktree cleans up what it created
    when verification fails", independent of *why* it failed.
    """
    worktree = paths.runs / "real-002" / "worktree"
    branch = "card/b001-02-atomic"

    def _boom(_project_dir: Path, _worktree_path: Path) -> None:
        raise WorktreeCreateError("forced verification failure")

    monkeypatch.setattr(worktree_mod, "_verify_worktree", _boom)

    with pytest.raises(WorktreeCreateError):
        prepare_worktree(
            paths=paths,
            project_dir=real_repo,
            branch_name=branch,
            base_branch="main",
            worktree_path=worktree,
            skip_git=False,
        )

    # The worktree we created must be gone from git's registry...
    assert worktree.resolve() not in _registered_worktrees(real_repo)

    # ...and, the point of the whole thing: a retry must be able to claim
    # the same branch again rather than livelocking on "already used".
    monkeypatch.undo()
    retry = paths.runs / "real-002-retry" / "worktree"
    prepare_worktree(
        paths=paths,
        project_dir=real_repo,
        branch_name=branch,
        base_branch="main",
        worktree_path=retry,
        skip_git=False,
    )
    assert retry.is_dir()
    assert retry.resolve() in _registered_worktrees(real_repo)
