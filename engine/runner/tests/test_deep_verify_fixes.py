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
import threading
import time
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
    """At `git init` the clone dirs are pinned but empty; the ancestor pins
    stop a swap of the attempt dir that would send the clone elsewhere."""
    project, base = _trusted_project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def swap_attempt_dir(cwd: Path) -> None:  # runs before `git init`, cwd = the project
        attempt = tmp_path / "state" / "clones" / "att1"
        os.rename(attempt, attempt.with_name("att1-aside"))
        _junction(attempt, elsewhere)

    seen = _swap_on(monkeypatch, "init", swap_attempt_dir)
    _integrate(project, base, tmp_path)
    assert isinstance(seen[0], PermissionError), seen
    assert list(elsewhere.iterdir()) == []


# ---- v2 re-verify L4-1-pre-pin: nothing is filled before it is pinned -------


def _stage_repo(where: Path) -> Path:
    """A sibling's pre-staged REAL dir (not a junction) with its own .git."""
    subprocess.run(["git", "init", "-q", str(where)], check=True)
    subprocess.run(["git", "-C", str(where), "config", "core.sshCommand", "evil"], check=True)
    return where


def _swap_before_pin(monkeypatch: pytest.MonkeyPatch, leaf: str, swap) -> list[str]:  # noqa: ANN001
    """Run `swap(path)` in the gap between the daemon's mkdir and its pin of
    the integration dir (`leaf` "integration") or of its .git (".git")."""
    seen: list[str] = []
    real = git_safety._Pins.pin

    def racing(self: git_safety._Pins, path: Path) -> None:
        p = Path(path)
        if p.name == leaf and "integration" in p.parts and not seen:
            swap(p)
            seen.append("swapped")
        real(self, path)

    monkeypatch.setattr(git_safety._Pins, "pin", racing)
    return seen


@pytest.mark.parametrize("leaf", ["integration", ".git"])
def test_a_real_dir_swapped_in_before_the_pin_is_refused(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch,
                                                         leaf: str) -> None:
    """The v2 gap was between `git clone` returning and the pin: a staged
    dir's .git/config was then pinned and baselined as trusted."""
    project, base = _trusted_project(tmp_path)
    staged = _stage_repo(tmp_path / "staged")
    staged_git = staged / ".git" if leaf == ".git" else staged

    def swap(path: Path) -> None:
        os.rmdir(path)  # the empty dir the daemon just made
        os.rename(staged_git, path)

    seen = _swap_before_pin(monkeypatch, leaf, swap)
    with pytest.raises(clones.CloneError, match="not empty once pinned"):
        _integrate(project, base, tmp_path)
    assert seen == ["swapped"]
    dest = tmp_path / "state" / "clones" / "att1" / "integration"
    assert git_safety._key(dest) not in git_safety._DAEMON_CLONES  # never trusted
    assert _g(project, "rev-parse", "main").strip() == base


def test_an_empty_dir_swapped_in_before_the_pin_is_harmless(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: what the emptiness check lets through is an empty dir, which
    git then fills under the pin, so the integration is the daemon's own."""
    project, base = _trusted_project(tmp_path)
    (tmp_path / "empty").mkdir()

    def swap(path: Path) -> None:
        os.rmdir(path)
        os.rename(tmp_path / "empty", path)

    seen = _swap_before_pin(monkeypatch, ".git", swap)
    sha = _integrate(project, base, tmp_path)
    assert seen == ["swapped"] and sha != base
    assert _g(project, "rev-parse", "main").strip() == base


def test_a_commondir_planted_in_a_daemon_clone_is_refused(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """With .git/commondir git writes refs and objects where it points (the
    project), while the fingerprint covers only the clone's own config."""
    project, base = _trusted_project(tmp_path)

    def plant(dest: Path) -> None:
        (dest / ".git" / "commondir").write_text(str(project / ".git") + "\n")

    _swap_on(monkeypatch, "commit", plant)
    with pytest.raises(git_safety.UntrustedRepo, match="commondir"):
        _integrate(project, base, tmp_path)
    assert _g(project, "rev-parse", "main").strip() == base


def _hold_delete_handle(path: Path, seconds: float) -> threading.Thread:
    """An opener with DELETE access (what an indexer or a sibling holds)."""
    import ctypes
    k32 = git_safety._kernel32()
    handle = k32.CreateFileW(str(path), 0x10000 | 0x80, 0x7, None, 3, 0x02000000, None)
    assert handle not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()
    thread = threading.Thread(target=lambda: (time.sleep(seconds), k32.CloseHandle(handle)))
    thread.start()
    return thread


@windows_only
def test_a_pin_retries_a_brief_sharing_violation_then_fails_closed(tmp_path: Path) -> None:
    """v2 re-verify L4-1-pin-dos: one DELETE opener blocked the card for good."""
    brief, held = tmp_path / "brief", tmp_path / "held"
    brief.mkdir()
    held.mkdir()
    pins = git_safety._Pins()
    try:
        thread = _hold_delete_handle(brief, 0.02)
        pins.pin(brief)  # released within the retry window
        thread.join()
        thread = _hold_delete_handle(held, 2.0)
        with pytest.raises(git_safety.ClonePinned, match="WinError 32"):
            pins.pin(held)
        thread.join()
    finally:
        pins.close()


def test_a_config_read_timeout_refuses_without_quarantine(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 re-verify L4-2-config-timeout: the timeout was hashed as a config
    change, and the PROJECT was quarantined persistently."""
    project, _ = _trusted_project(tmp_path)
    real = subprocess.run

    def slow(argv, *a: Any, **kw: Any):  # noqa: ANN001, ANN202
        if "--file" in argv:
            raise subprocess.TimeoutExpired(argv, 1)
        return real(argv, *a, **kw)

    monkeypatch.setattr(git_safety.subprocess, "run", slow)
    with pytest.raises(git_safety.GitUnavailable):
        git_safety.assert_trusted(project)
    monkeypatch.setattr(git_safety.subprocess, "run", real)
    git_safety.assert_trusted(project)  # not quarantined: trusted again once git answers
    record = git_safety.trust_record(git_safety.common_dir_of(project))
    assert record is not None and not record.quarantined


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


# ---- process and env (L3-02, L3-03, L5-F1, L5-F2) -------------------------


def test_shell_output_is_text_mode_again(tmp_path: Path) -> None:
    """L3-02: an AC expecting 'line1\\nline2' passed on main and failed on
    cmd.exe's CRLF after the launcher seam."""
    from cards_runner.verifier.handlers.deterministic import HandlerContext, handle_shell
    from cards_runner.common.sandbox import LocalProcessLauncher
    out = handle_shell({"command": "echo line1& echo line2", "expect_contains": "line1\nline2"},
                       HandlerContext(worktree=tmp_path, env={}, launcher=LocalProcessLauncher()))
    assert out.passed, out.evidence


def test_a_lone_cr_is_a_newline_as_under_text_mode(tmp_path: Path) -> None:
    """#87 v2 V2-2: text=True used universal newlines; v2 mapped only CRLF."""
    from cards_runner.verifier.handlers.deterministic import HandlerContext, handle_shell
    from cards_runner.common.sandbox import LocalProcessLauncher
    script = tmp_path / "w.py"
    script.write_text("import sys\nsys.stdout.buffer.write(b'x\\ry\\r\\nz\\n')\n")
    # Unquoted and relative: cmd.exe strips a leading quote pair oddly.
    out = handle_shell({"command": f"{sys.executable} {script.name}", "expect_contains": "x\ny\nz"},
                       HandlerContext(worktree=tmp_path, env={}, launcher=LocalProcessLauncher()))
    assert out.passed, out.evidence


def test_the_ext_transport_stays_off_whatever_the_repo_says(tmp_path: Path) -> None:
    """M20: protocol.ext.allow=never had no test. A repo config turning ext
    on is overridden for the daemon's git, and not for plain git (control)."""
    project, _ = _trusted_project(tmp_path)
    _g(project, "config", "protocol.ext.allow", "always")
    git_safety.trust_repo(git_safety.common_dir_of(project))
    url = "ext::git --version"
    plain = subprocess.run(["git", "ls-remote", url], cwd=project, capture_output=True, text=True)
    assert "not allowed" not in plain.stderr  # control: the repo's setting reached git
    daemon = git_safety.run_git([git_safety.resolved("git"), "ls-remote", url], cwd=project)
    assert daemon.returncode != 0 and "transport 'ext' not allowed" in daemon.stderr


@windows_only
def test_a_command_inherits_only_its_std_handles(tmp_path: Path) -> None:
    """L3-03: with no handle list, a child wrote through an inheritable
    daemon handle it was never given."""
    import msvcrt
    from cards_runner.common.sandbox import LocalProcessLauncher
    target = tmp_path / "daemon-owned.log"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT)
    os.set_inheritable(fd, True)
    try:
        handle = msvcrt.get_osfhandle(fd)
        code = ("import msvcrt, os\n"
                f"fd = msvcrt.open_osfhandle({handle}, 0)\n"
                "os.write(fd, b'forged')\n")
        res = LocalProcessLauncher().run_command([sys.executable, "-c", code], cwd=tmp_path,
                                                 env=dict(os.environ), timeout=30)
    finally:
        os.close(fd)
    assert target.read_bytes() == b"", "the child wrote through a handle it was not given"
    assert res.returncode != 0  # control: the attempt itself ran


def test_a_command_sees_only_the_env_it_was_given(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """L5-F2: nothing pinned that the launcher honors `env`."""
    from cards_runner.common.sandbox import LocalProcessLauncher
    monkeypatch.setenv("CARDS_TEST_DAEMON_ONLY", "leak")
    res = LocalProcessLauncher().run_command(["cmd", "/c", "set"], cwd=tmp_path,
                                             env={"CARDS_TEST_GIVEN": "1"}, timeout=30)
    assert "CARDS_TEST_GIVEN=1" in res.stdout and "CARDS_TEST_DAEMON_ONLY" not in res.stdout


def test_the_verifier_gets_the_hardened_empty_env(setup, repo, paths,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """L5-F1/L5-F2: no secrets, and git off system and global config."""
    project, gh, daemon = setup
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    monkeypatch.setenv("GH_TOKEN", "gho_test0042")
    _insert(repo, "bTST-DV-env", project)
    claim = _spawned(daemon, repo, paths, "bTST-DV-env", monkeypatch)
    (claim.worktree_path / "anything").write_text("x\n", encoding="utf-8")
    assert emit_patch(worktree=claim.worktree_path, base_sha=claim.base_sha, run_dir=claim.run_dir)
    import cards_runner.daemon.daemon as dm
    seen: list[dict[str, str]] = []
    real = dm.verify_card
    monkeypatch.setattr(dm, "verify_card", lambda **kw: (seen.append(kw["env"]), real(**kw))[1])
    _exit(daemon, claim)
    assert seen and seen[0] == git_safety.verifier_env()
    assert not any("sk-ant" in v or "gho_" in v for v in seen[0].values())


def test_a_shell_ac_git_cannot_see_a_system_credential_helper(tmp_path: Path) -> None:
    git = git_safety.resolved("git")
    cmd = [git, "config", "--get-all", "credential.helper"]
    control = subprocess.run(cmd, cwd=tmp_path, env={}, capture_output=True, text=True)
    if not control.stdout.strip():
        pytest.skip("no system or global credential.helper on this machine: no control")
    hardened = subprocess.run(cmd, cwd=tmp_path, env=git_safety.verifier_env(),
                              capture_output=True, text=True)
    assert hardened.stdout.strip() == "", hardened.stdout


# ---- git hardening (L4-3, L4-4, gitexec-F2, other-branch objects) ---------


def test_a_planted_replace_ref_is_not_honored(tmp_path: Path) -> None:
    """L4-3: refs/replace swapped what the committed-tree check saw."""
    project, base = _trusted_project(tmp_path)
    (project / "other.txt").write_text("decoy\n")
    _g(project, "add", "other.txt")
    _g(project, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "d")
    decoy = _g(project, "rev-parse", "HEAD").strip()
    _g(project, "reset", "-q", "--hard", base)
    git_safety.trust_repo(git_safety.common_dir_of(project))  # the plant is not config: still trusted
    _g(project, "replace", base, decoy)
    plain = _g(project, "ls-tree", "--name-only", base).split()
    assert "other.txt" in plain  # control: plain git shows the decoy's tree for `base`
    hardened = git_safety.run_git(["git", "ls-tree", "--name-only", base],
                                  cwd=project).stdout.split()
    assert "other.txt" not in hardened


def test_hooks_run_from_nowhere_the_daemon_can_be_raced_on(tmp_path: Path) -> None:
    """L4-4: the emptied hooks dir was writable between emptying and use."""
    env = git_safety.hardened_git_env({})
    pinned = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
              for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pinned["core.hooksPath"] == os.devnull
    project, _ = _trusted_project(tmp_path)
    marker = tmp_path / "hook-ran"
    hooks = project / ".git" / "hooks"
    (hooks / "post-commit").write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n",
                                       newline="\n")
    git_safety.trust_repo(git_safety.common_dir_of(project))
    (project / "f.txt").write_text("x\n")
    git_safety.run_git(["git", "add", "f.txt"], cwd=project)
    git_safety.run_git(["git", "commit", "-q", "-m", "x"], cwd=project,
                       extra_config=tuple(git_safety.commit_identity()))
    assert not marker.exists()
    _g(project, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q",
       "--allow-empty", "-m", "control")
    assert marker.exists()  # control: plain git runs the hook


def test_an_edit_to_a_conditional_include_target_changes_the_fingerprint(tmp_path: Path) -> None:
    """gitexec-F2: includeIf targets were not hashed, so redirecting
    remote.origin.url there went unseen."""
    project, _ = _trusted_project(tmp_path)
    target = tmp_path / "included.cfg"
    target.write_text("[remote \"origin\"]\n\turl = https://example.invalid/o/r.git\n")
    _g(project, "config", f"includeIf.gitdir:{project.as_posix()}/.path", target.as_posix())
    common = git_safety.common_dir_of(project)
    before = git_safety.fingerprint(common)
    target.write_text("[remote \"origin\"]\n\turl = C:/decoy/bare.git\n")
    assert git_safety.fingerprint(common) != before
    nested = tmp_path / "nested.cfg"
    target.write_text(f"[includeIf \"gitdir:{project.as_posix()}/\"]\n\tpath = {nested.as_posix()}\n")
    mid = git_safety.fingerprint(common)
    nested.write_text("[core]\n\tsshCommand = evil\n")
    assert git_safety.fingerprint(common) != mid  # nested conditional targets too


def test_a_worker_clone_holds_no_other_branch_objects(tmp_path: Path) -> None:
    project, _ = _trusted_project(tmp_path)
    _g(project, "checkout", "-q", "-b", "other")
    (project / "secret-branch.txt").write_text("not for the worker\n")
    _g(project, "add", "-A")
    _g(project, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "o")
    other = _g(project, "rev-parse", "other").strip()
    _g(project, "checkout", "-q", "main")
    git_safety.trust_repo(git_safety.common_dir_of(project))
    wt = tmp_path / "wt"
    clones.prepare_worker_clone(project_dir=project, base_branch="main", branch="card/x/1", dest=wt)
    assert subprocess.run(["git", "cat-file", "-e", other], cwd=wt).returncode != 0
    assert subprocess.run(["git", "cat-file", "-e", other], cwd=project).returncode == 0  # control


# ---- patches (patch-F1, patch-F3) ------------------------------------------


def _new_file_patch(path: str, content: bytes = b"shadow\n") -> bytes:
    blob = subprocess.run(["git", "hash-object", "--stdin"], input=content, capture_output=True,
                          check=True).stdout.decode().strip()
    body = b"".join(b"+" + line + b"\n" for line in content.rstrip(b"\n").split(b"\n"))
    n = content.count(b"\n")
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\nindex {'0' * 40}..{blob}\n"
            f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{n} @@\n").encode("utf-8") + body


def _apply(project: Path, base: str, tmp_path: Path, patch: bytes, dest: str = "int") -> str:
    pf = tmp_path / f"{dest}.patch"
    pf.write_bytes(patch)
    return integrate_patch(project_dir=project, base_branch="main", base_sha=base,
                           branch="card/x/1", patch_file=pf, dest=tmp_path / dest, message="m")


@pytest.mark.parametrize("path", ["Readme.md", "readme.MD", "Tests/t.py", "Tests/u.py"],
                         ids=["case", "case-ext", "case-path", "case-dir-only"])
def test_a_path_that_collides_on_a_case_insensitive_disk_is_refused(tmp_path: Path,
                                                                    path: str) -> None:
    """patch-F1: README.md and Readme.md both committed; the NTFS verify
    clone held one of them, so the verifier checked a different tree."""
    project, base = _trusted_project(tmp_path)
    if path.startswith("Tests/"):
        (project / "tests").mkdir()
        (project / "tests" / "t.py").write_text("x = 1\n")
        _g(project, "add", "-A")
        _g(project, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "t")
        base = _g(project, "rev-parse", "HEAD").strip()
        git_safety.trust_repo(git_safety.common_dir_of(project))
    with pytest.raises(clones.PatchRejected, match="collide"):
        _apply(project, base, tmp_path, _new_file_patch(path))


def test_an_nfd_twin_of_an_nfc_name_is_refused(tmp_path: Path) -> None:
    import unicodedata
    project, base = _trusted_project(tmp_path)
    nfc = unicodedata.normalize("NFC", "café.txt")
    nfd = unicodedata.normalize("NFD", nfc)
    patch = _new_file_patch(nfc) + _new_file_patch(nfd, b"twin\n")
    with pytest.raises(clones.PatchRejected, match="collide"):
        _apply(project, base, tmp_path, patch)


def test_a_case_only_rename_and_an_old_collision_are_not_blocked(tmp_path: Path) -> None:
    """Controls: only collisions the patch ADDS are refused."""
    project, base = _trusted_project(tmp_path)
    rename = _patch_for(tmp_path, lambda r: (_g(r, "mv", "README.md", "tmp.md"),
                                             _g(r, "mv", "tmp.md", "Readme.md")))
    assert _apply(project, base, tmp_path, rename, "int1") != base
    # A base that already holds a collision (made through the index, not the disk).
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=project, input=b"twin\n",
                          capture_output=True, check=True).stdout.decode().strip()
    _g(project, "update-index", "--add", "--cacheinfo", f"100644,{blob},Readme.md")
    _g(project, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "c")
    collided = _g(project, "rev-parse", "HEAD").strip()
    names = _g(project, "ls-tree", "--name-only", collided).split()
    assert "README.md" in names and "Readme.md" in names  # the base really collides
    git_safety.trust_repo(git_safety.common_dir_of(project))
    unrelated = _patch_for(tmp_path, lambda r: (r / "src.py").write_text("x = 1\n"))
    assert _apply(project, collided, tmp_path, unrelated, "int2") != collided


def test_binary_hunks_that_inflate_past_the_cap_are_refused_before_apply(tmp_path: Path) -> None:
    """patch-F3: 6 MB of patch inflated to 1 GiB in git apply."""
    project, base = _trusted_project(tmp_path)
    real = _patch_for(tmp_path, lambda r: (r / "b.bin").write_bytes(b"\0" * 4096))
    assert b"literal 4096" in real  # the size line the cap reads
    assert _apply(project, base, tmp_path, real, "ok") != base  # control: a small binary applies
    huge = real.replace(b"literal 4096", f"literal {clones.PATCH_MAX_INFLATED_BYTES + 1}".encode())
    # Anchored to the cap's own message: the edited size line also corrupts
    # the hunk, and git's own refusal mentions "inflate" too.
    with pytest.raises(clones.PatchRejected, match=r"^binary hunks inflate to"):
        _apply(project, base, tmp_path, huge, "huge")


def _commit_file(repo: Path, name: str, data: bytes) -> str:
    (repo / name).write_bytes(data)
    _g(repo, "add", name)
    _g(repo, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", name)
    return _g(repo, "rev-parse", "HEAD").strip()


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _b85_hunk(data: bytes) -> bytes:
    import base64
    out = []
    for i in range(0, len(data), 52):
        chunk = data[i:i + 52]
        letter = chr(ord("A") + len(chunk) - 1) if len(chunk) <= 26 else chr(ord("a") + len(chunk) - 27)
        out.append(letter.encode() + base64.b85encode(chunk + b"\0" * (-len(chunk) % 4)))
    return b"\n".join(out) + b"\n"


def _copy_delta_patch(base: bytes, copies: int) -> bytes:
    """The V2-1 shape: a delta of `copies` copy ops of the whole base, so a
    few KB of patch builds len(base) * copies bytes."""
    import hashlib
    import zlib
    size = len(base) * copies
    delta = _varint(len(base)) + _varint(size) + bytes([0x90, len(base)]) * copies
    new = hashlib.sha1(b"blob %d\0" % size)
    for _ in range(copies):
        new.update(base)
    old = hashlib.sha1(b"blob %d\0" % len(base) + base).hexdigest()
    return (f"diff --git a/big.bin b/big.bin\nindex {old}..{new.hexdigest()} 100644\n"
            f"GIT binary patch\ndelta {len(delta)}\n").encode() + _b85_hunk(zlib.compress(delta, 9)) + \
        f"\nliteral {len(base)}\n".encode() + _b85_hunk(zlib.compress(base, 9)) + b"\n"


def test_a_binary_delta_is_capped_by_the_result_it_builds(tmp_path: Path) -> None:
    """#87 v2 V2-1: the cap read only the delta's own size, so 3.7 KB of
    patch built and committed a 260 MB blob."""
    project, _ = _trusted_project(tmp_path)
    base_bytes = bytes(range(33, 233))
    base = _commit_file(project, "big.bin", base_bytes)
    git_safety.trust_repo(git_safety.common_dir_of(project))
    small = _copy_delta_patch(base_bytes, 10)
    assert _apply(project, base, tmp_path, small, "ok") != base  # control: git takes this shape
    copies = clones.PATCH_MAX_INFLATED_BYTES // len(base_bytes) + 1
    huge = _copy_delta_patch(base_bytes, copies)
    stated = int(huge.split(b"delta ")[1].split(b"\n")[0])
    assert stated < clones.PATCH_MAX_INFLATED_BYTES  # the delta line alone passes the old cap
    with pytest.raises(clones.PatchRejected, match=r"^binary hunks inflate to"):
        _apply(project, base, tmp_path, huge, "huge")


def test_a_delta_git_itself_wrote_still_applies(tmp_path: Path) -> None:
    """Control for the header decode: a real `git diff --binary` delta."""
    import random
    project, _ = _trusted_project(tmp_path)
    data = random.Random(7).randbytes(20000)
    base = _commit_file(project, "blob.bin", data)
    git_safety.trust_repo(git_safety.common_dir_of(project))
    scratch = tmp_path / "scratch"
    subprocess.run(["git", "init", "-q", str(scratch)], check=True)
    old = _commit_file(scratch, "blob.bin", data)
    (scratch / "blob.bin").write_bytes(data[:100] + b"changed" + data[107:])
    patch = subprocess.run(["git", "diff", "--binary", "--full-index", old], cwd=scratch,
                           capture_output=True, check=True).stdout
    assert b"\ndelta " in patch  # git chose a delta, so the decode path ran
    assert _apply(project, base, tmp_path, patch, "real") != base


def test_a_patch_over_the_file_count_is_refused(tmp_path: Path) -> None:
    project, base = _trusted_project(tmp_path)
    patch = b"".join(_new_file_patch(f"f{i}.txt") for i in range(clones.PATCH_MAX_FILES + 1))
    with pytest.raises(clones.PatchRejected, match="files, over"):
        _apply(project, base, tmp_path, patch)


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
