# ruff: noqa: F811 - the `setup` fixture is imported by name, the pytest idiom
"""Fixes for the deep verify of #87: one isolating test per defense.

L4-1 (blocker): a sibling worker swapped a daemon clone dir for a junction
to the project, and the daemon committed into the operator's checkout.
Two independent layers close it, and each has its own test with the other
layer switched off: the pins make the rename impossible; the moment-of-use
check refuses a swapped clone even when nothing pinned it.
L4-2 (blocker): one undecodable filename or a git timeout wedged the tick.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cards_runner.common import git_safety
from cards_runner.daemon import clones
from cards_runner.daemon.clones import integrate_patch
from cards_runner.daemon.daemon import _WorkerHandle
from cards_runner.store import CardStatus
from cards_runner.worker_stub.patch_emit import emit_patch

from tests.test_clone_per_worker import (  # noqa: F401  (fixtures + helpers)
    _claim, _exit, _g, _insert, _patch_for, _project, _spawned, setup,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows directory pins")


def _junction(link: Path, target: Path) -> None:
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                   capture_output=True)


def _trusted_project(tmp_path: Path) -> tuple[Path, str]:
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    return project, _g(project, "rev-parse", "main").strip()


def _swap_on(monkeypatch: pytest.MonkeyPatch, step: str, swap) -> list[Any]:  # noqa: ANN001
    """Run `swap(cwd)` as a sibling would, just before the daemon's git `step`."""
    seen: list[Any] = []
    real = clones._git

    def racing(cwd: Path, *args: str, **kw: Any) -> str:
        if args and args[0] == step and not seen:
            try:
                swap(Path(cwd))
                seen.append("swapped")
            except OSError as exc:
                seen.append(exc)
        return real(cwd, *args, **kw)

    monkeypatch.setattr(clones, "_git", racing)
    return seen


def _sibling_swap(project: Path):  # noqa: ANN202
    def swap(dest: Path) -> None:
        os.rename(dest, dest.with_name(dest.name + "-aside"))
        _junction(dest, project)
    return swap


def _integrate(project: Path, base: str, tmp_path: Path) -> str:
    pf = tmp_path / "p.patch"
    pf.write_bytes(_patch_for(tmp_path, lambda r: (r / "src.py").write_text("x = 1\n")))
    return integrate_patch(project_dir=project, base_branch="main", base_sha=base,
                           branch="card/x/1", patch_file=pf,
                           dest=tmp_path / "state" / "clones" / "att1" / "integration",
                           message="m")


@windows_only
def test_a_pinned_clone_cannot_be_renamed_aside(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    project, base = _trusted_project(tmp_path)
    seen = _swap_on(monkeypatch, "commit", _sibling_swap(project))
    sha = _integrate(project, base, tmp_path)
    assert isinstance(seen[0], PermissionError), seen  # the rename was refused
    assert _g(project, "rev-parse", "main").strip() == base  # project untouched
    assert _g(project, "status", "--porcelain").strip() == ""
    assert sha != base


@windows_only
def test_the_attempt_dir_above_the_clone_is_pinned_too(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    project, base = _trusted_project(tmp_path)

    def swap_parent(dest: Path) -> None:
        os.rename(dest.parent, dest.parent.with_name("att1-aside"))

    seen = _swap_on(monkeypatch, "commit", swap_parent)
    _integrate(project, base, tmp_path)
    assert isinstance(seen[0], PermissionError), seen


@windows_only
def test_the_clone_dot_git_itself_cannot_be_renamed_aside(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The leaf: nothing is open beneath `.git`, so only its own pin's share
    mode (no FILE_SHARE_DELETE) stops a junction to the project's .git."""
    project, base = _trusted_project(tmp_path)

    def swap_git(dest: Path) -> None:
        os.rename(dest / ".git", dest / "git-aside")
        _junction(dest / ".git", project / ".git")

    seen = _swap_on(monkeypatch, "commit", swap_git)
    _integrate(project, base, tmp_path)
    assert isinstance(seen[0], PermissionError), seen
    assert _g(project, "rev-parse", "main").strip() == base


@windows_only
def test_the_attempt_dir_is_pinned_before_the_clone_exists(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Before `git clone` there is no clone handle beneath the attempt dir, so
    only the ancestor pins stop a swap that would send the clone elsewhere."""
    project, base = _trusted_project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def swap_attempt_dir(cwd: Path) -> None:  # runs before `git clone`, cwd = the project
        attempt = tmp_path / "state" / "clones" / "att1"
        os.rename(attempt, attempt.with_name("att1-aside"))
        _junction(attempt, elsewhere)

    seen = _swap_on(monkeypatch, "clone", swap_attempt_dir)
    _integrate(project, base, tmp_path)
    assert isinstance(seen[0], PermissionError), seen
    assert list(elsewhere.iterdir()) == []


@windows_only
def test_unpinned_the_swap_happens_and_the_moment_of_use_check_refuses(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second layer alone: pins switched off, the real swap succeeds."""
    project, base = _trusted_project(tmp_path)
    monkeypatch.setattr(git_safety._Pins, "pin", lambda self, path: None)
    seen = _swap_on(monkeypatch, "commit", _sibling_swap(project))
    with pytest.raises(git_safety.UntrustedRepo, match="replaced by a link|resolves to"):
        _integrate(project, base, tmp_path)
    assert seen == ["swapped"]  # control: without pins the sibling did get through
    assert _g(project, "rev-parse", "main").strip() == base
    assert _g(project, "status", "--porcelain").strip() == ""
    common = git_safety.common_dir_of(project)
    assert git_safety.trust_record(common) is not None  # the project's record survived


@windows_only
def test_the_daemon_holds_the_pins_from_integration_through_the_push(
        setup, repo, paths, monkeypatch: pytest.MonkeyPatch) -> None:
    """integrate_patch's own pins end when it returns; the daemon's pin set
    must keep the integration clone pinned while it verifies and pushes."""
    project, gh, daemon = setup
    _insert(repo, "bTST-DV-hold", project)
    claim = _spawned(daemon, repo, paths, "bTST-DV-hold", monkeypatch)
    (claim.worktree_path / "anything").write_text("x\n", encoding="utf-8")
    assert emit_patch(worktree=claim.worktree_path, base_sha=claim.base_sha, run_dir=claim.run_dir)
    import cards_runner.daemon.daemon as dm
    real = dm.prepare_verify_clone
    seen: list[Any] = []

    def sibling_between_integrate_and_verify(*, integration: Path, **kw: Any) -> None:
        try:
            os.rename(integration, integration.with_name("integration-aside"))
            seen.append("renamed")
        except OSError as exc:
            seen.append(exc)
        return real(integration=integration, **kw)

    monkeypatch.setattr(dm, "prepare_verify_clone", sibling_between_integrate_and_verify)
    _exit(daemon, claim)
    assert isinstance(seen[0], PermissionError), seen
    assert [n for n, _ in gh.calls].count("push") == 1


def test_a_clone_whose_dot_git_is_not_its_own_is_never_trusted(tmp_path: Path) -> None:
    project, _ = _trusted_project(tmp_path)
    fake = tmp_path / "clone"
    fake.mkdir()
    (fake / ".git").write_text(f"gitdir: {project / '.git'}\n", encoding="utf-8")
    with pytest.raises(git_safety.UntrustedRepo, match="not its own .git"):
        git_safety.trust_daemon_clone(fake)


def test_forget_uses_the_recorded_pairing_not_a_rewritten_dot_git(tmp_path: Path) -> None:
    """gitexec-F1: forget() re-read .git, so an AC could make it drop the
    project's in-memory record."""
    project, base = _trusted_project(tmp_path)
    clone = tmp_path / "clone"
    _g(tmp_path, "clone", "-q", "--no-hardlinks", str(project), str(clone))
    git_safety.trust_daemon_clone(clone)
    clone_common = git_safety.common_dir_of(clone)
    proj_common = git_safety.common_dir_of(project)
    # An AC rewrites the clone's .git to point at the project.
    git_dir = clone / ".git"
    git_dir.rename(tmp_path / "moved-git")
    git_dir.write_text(f"gitdir: {project / '.git'}\n", encoding="utf-8")
    git_safety.forget(clone)
    with git_safety._LOCK:
        assert str(proj_common) in git_safety._MEMORY  # the project kept its record
        assert str(clone_common) not in git_safety._MEMORY


# ---- L4-2: the tick never wedges ------------------------------------------


def test_a_filename_the_locale_codec_cannot_decode_integrates(tmp_path: Path) -> None:
    project, base = _trusted_project(tmp_path)
    pf = tmp_path / "p.patch"
    pf.write_bytes(_patch_for(tmp_path, lambda r: (r / "Łódź.txt").write_text("x\n")))
    sha = integrate_patch(project_dir=project, base_branch="main", base_sha=base,
                          branch="card/x/1", patch_file=pf, dest=tmp_path / "int", message="m")
    # Read back as UTF-8: the test helper's locale decode has the very bug fixed here.
    tree = subprocess.run(["git", "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", sha],
                          cwd=tmp_path / "int", capture_output=True, check=True).stdout
    assert "Łódź.txt" in tree.decode("utf-8")


def test_a_git_timeout_in_integration_blocks_the_card(setup, repo, paths,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    project, gh, daemon = setup
    _insert(repo, "bTST-DV-timeout", project)
    claim = _spawned(daemon, repo, paths, "bTST-DV-timeout", monkeypatch)
    (claim.worktree_path / "anything").write_text("x\n", encoding="utf-8")
    assert emit_patch(worktree=claim.worktree_path, base_sha=claim.base_sha, run_dir=claim.run_dir)

    def hang(**kw: Any) -> str:
        raise subprocess.TimeoutExpired(cmd="git apply", timeout=300)

    monkeypatch.setattr("cards_runner.daemon.daemon.integrate_patch", hang)
    _exit(daemon, claim)
    card = repo.get_card("bTST-DV-timeout")
    assert card is not None and card.status == CardStatus.BLOCKED.value
    assert gh.calls == []


def test_one_failing_post_exit_does_not_stop_the_next_card(setup, repo, paths,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    project, gh, daemon = setup

    class Done:
        def poll(self) -> int:
            return 0

        def end_job(self) -> None:
            pass

    claims = []
    for cid in ("bTST-DV-a", "bTST-DV-b"):
        _insert(repo, cid, project)
        claim = _claim(repo, paths, cid)
        daemon._workers[claim.attempt_trace_id] = _WorkerHandle(claim=claim, process=Done(),
                                                                spawned_at=0.0)
        claims.append(claim)
    handled: list[str] = []

    def post_exit(handle: _WorkerHandle, rc: int) -> None:
        handled.append(handle.claim.card_id)
        if handle.claim.card_id == "bTST-DV-a":
            raise AttributeError("'NoneType' object has no attribute 'split'")  # the L4-2 shape

    monkeypatch.setattr(daemon, "_post_worker_exit", post_exit)
    daemon._reap_workers({"worker_exits": 0})
    assert handled == ["bTST-DV-a", "bTST-DV-b"]  # the second card was still handled
    assert daemon._workers == {}  # both handles popped: neither is re-run next tick
    card = repo.get_card("bTST-DV-a")
    assert card is not None and card.status == CardStatus.BLOCKED.value
