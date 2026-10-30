"""Worker tree preparation: the skip-mode stub, branch names, and real clones.

Real cards get a throwaway clone (daemon/clones.py; the security side is
in test_clone_per_worker.py). These cover the lifecycle properties the
old `git worktree` tests pinned: unique per-attempt branches, a retry
alongside a kept forensic tree, and a failed prep that rolls back.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cards_runner.common import git_safety
from cards_runner.common.types import RuntimePaths
from cards_runner.daemon.clones import CloneError, prepare_worker_clone
from cards_runner.daemon.worktree import attempt_branch_name, prepare_stub_worktree

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="needs git")


@pytest.fixture
def real_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on `main`."""
    assert GIT is not None
    repo = tmp_path / "proj"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["-c", "user.email=t@example.test", "-c", "user.name=t",
                  "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run([GIT, *args], cwd=repo, check=True, capture_output=True)
    return repo


def test_prepare_stub_worktree_creates_dir(paths: RuntimePaths) -> None:
    wt = paths.runs / "att-stub" / "worktree"
    prepare_stub_worktree(worktree_path=wt, branch_name="card/x/1", base_branch="main")
    assert wt.is_dir()
    assert (wt / ".cards-stub-worktree").read_text(encoding="utf-8") == (
        "branch=card/x/1\nbase=main\n")
    assert not (wt / ".git").exists()


def test_attempt_branch_name_is_unique_per_attempt() -> None:
    """Two attempts of the same card get distinct branches, both keeping
    the card-identifying prefix, so a bounced card retries instead of
    colliding on an earlier attempt's branch."""
    base = "card/b001-01-add-health-check"
    a = attempt_branch_name(base, "aaaaaaaaaaaa1111")
    b = attempt_branch_name(base, "bbbbbbbbbbbb2222")
    assert a != b
    assert a.startswith(base + "/") and b.startswith(base + "/")
    # Deterministic: clone prep and the merge gate derive it independently
    # and MUST agree, or the gate pushes a branch prep never created.
    assert attempt_branch_name(base, "aaaaaaaaaaaa1111") == a


@needs_git
def test_a_retry_gets_its_own_clone_beside_the_kept_one(
    paths: RuntimePaths, real_repo: Path,
) -> None:
    base = "card/b001-03-retry"
    wt1 = paths.runs / "att-1" / "worktree"
    wt2 = paths.runs / "att-2" / "worktree"
    sha1 = prepare_worker_clone(project_dir=real_repo, base_branch="main",
                                branch=attempt_branch_name(base, "1111" * 4), dest=wt1)
    # attempt 1's clone is deliberately kept (forensic TTL).
    sha2 = prepare_worker_clone(project_dir=real_repo, base_branch="main",
                                branch=attempt_branch_name(base, "2222" * 4), dest=wt2)
    assert sha1 == sha2
    assert (wt1 / ".git").is_dir() and (wt2 / ".git").is_dir()
    # The project gains no branch and no worktree registration from either.
    branches = subprocess.run([GIT, "branch", "--format=%(refname:short)"], cwd=real_repo,
                              check=True, capture_output=True, text=True).stdout.split()
    assert branches == ["main"]
    assert not (real_repo / ".git" / "worktrees").exists()


@needs_git
def test_prep_fails_cleanly_without_a_repo_or_a_base(paths: RuntimePaths, tmp_path: Path,
                                                    real_repo: Path) -> None:
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(CloneError, match="not a git repo"):
        prepare_worker_clone(project_dir=not_a_repo, base_branch="main", branch="card/x/1",
                             dest=paths.runs / "a" / "worktree")
    with pytest.raises(CloneError):
        prepare_worker_clone(project_dir=real_repo, base_branch="no-such-branch",
                             branch="card/x/1", dest=paths.runs / "b" / "worktree")
    assert not (paths.runs / "b" / "worktree").exists()
    # The project itself stays trusted: a bad base is not a tamper.
    git_safety.assert_trusted(real_repo)
