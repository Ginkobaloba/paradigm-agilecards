"""Secrets must not reach model-written code, and git must not run it.

Threat model: prompt injection through card text or repo files steers a
tool-mode executor. Everything it can make run (tool shells, git hooks,
git config helpers, diff drivers) must see no Anthropic key and no
daemon credentials, and the daemon's own git calls in that worktree must
not execute anything the executor planted.

Each git-behaviour test runs a POSITIVE CONTROL first: the same plant
fires under a plain git call, so a pass means the hardening stopped it,
not that the plant never worked on this host.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cards_runner.common import git_safety
from cards_runner.common.canonical_config import ENV_TIER_MAP_PATH
from cards_runner.common.types import ClaimedCard, DaemonConfig
from cards_runner.daemon import spawner
from cards_runner.daemon.diff_stats import DiffStats
from cards_runner.daemon.pr_lifecycle import SubprocessGhRunner
from cards_runner.worker_stub.sdk_invoker import SdkInvoker
from cards_runner.worker_stub.tools import ToolBelt, ToolError, _git_config_is_read_only

FAKE_KEY = "sk-ant-test-isolation-marker-0042"


@pytest.fixture(autouse=True)
def _git_state(tmp_path: Path) -> Path:
    """Each test gets its own trust store and empty config files."""
    state = tmp_path / "git-state"
    git_safety.configure(state)
    return state
GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not on PATH")


# ---- (a) the worker and its tool shell -------------------------------------

def test_from_env_takes_the_key_out_of_the_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    inv = SdkInvoker.from_env()
    assert inv.api_key == FAKE_KEY
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_the_tool_shell_cannot_print_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real shell tool, the way a prompt-injected model would use it.
    # Even with the key still in os.environ (the pre-fix worker state),
    # the belt's env is a fresh scrub.
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GH_TOKEN", "gh-decoy-isolation-0042")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-decoy-isolation-0042")
    inv = SdkInvoker(api_key=FAKE_KEY)
    belt = inv._build_tool_belt(SimpleNamespace(worktree=str(tmp_path)))  # type: ignore[arg-type]
    cmd = "set" if sys.platform == "win32" else "env"
    out = belt.execute("shell", {"command": cmd})
    dump = str(out.payload)
    assert out.ok
    assert FAKE_KEY not in dump
    assert "gh-decoy-isolation-0042" not in dump
    assert "openai-decoy-isolation-0042" not in dump
    assert "PATH" in dump.upper()  # the shell still works


@needs_git
def test_the_tool_shell_writes_a_throwaway_profile_not_the_real_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # F2 at the source: `git config --global` from a model-run shell.
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    real_cfg = real_home / ".gitconfig"
    real_cfg.write_text("[user]\n\tname = Owner\n\temail = owner@example.test\n")
    original = real_cfg.read_text()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(real_home))
    for var in ("GIT_CONFIG_GLOBAL", "XDG_CONFIG_HOME", "HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(var, raising=False)
    plant = "git config --global core.sshCommand evil-isolation-0042"

    # Positive control: the plain scrubbed env (the r1 belt) writes the real file.
    from cards_runner.common.env_scrub import scrub_environment
    subprocess.run(plant, shell=True, check=True, env=scrub_environment(base=dict(os.environ)))
    assert "evil-isolation-0042" in real_cfg.read_text(), "control: plant should land"
    real_cfg.write_text(original)

    worktree = tmp_path / "run" / "worktree"
    worktree.mkdir(parents=True)
    belt = SdkInvoker(api_key=FAKE_KEY)._build_tool_belt(
        SimpleNamespace(worktree=str(worktree)))  # type: ignore[arg-type]
    out = belt.execute("shell", {"command": plant})
    assert out.ok, out.payload
    assert real_cfg.read_text() == original, "the tool shell wrote the real profile"
    throwaway = (tmp_path / "run" / "tool-home" / ".gitconfig").read_text()
    assert "evil-isolation-0042" in throwaway
    # and the owner's commit identity came along, so commits attribute as before
    assert "owner@example.test" in throwaway and "Owner" in throwaway


# ---- (b) the spawner only hands the key to a worker that can use it -------

def _tier_map(tmp: Path, model: str) -> Path:
    path = tmp / "tier_map.yaml"
    lines = ["version: 1", "tiers:"] + [f"  {p}:\n    model: {model}" for p in range(1, 7)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _spawn_env(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    seen: dict[str, str] = {}
    monkeypatch.setattr(spawner, "spawn_in_job", lambda args, *, env, **_: seen.update(env) or object())
    run_dir = tmp / "run"
    claim = ClaimedCard(card_id="c", attempt_trace_id="a", trace_id="t", run_dir=run_dir,
                        worktree_path=tmp, card_file=run_dir / "card.md")
    spawner.spawn_worker(cfg=DaemonConfig(todo_root=tmp, invoker="sdk"), claim=claim, run_dir=run_dir)
    return seen


@pytest.mark.parametrize(
    ("model", "tools", "expect_key"),
    [
        ("ollama/qwen3:30b", False, False),   # all local: no key at all
        ("ollama/qwen3:30b", True, True),     # tool loop is Anthropic-only
        ("claude-sonnet-4-6", False, True),   # a hosted tier needs it
    ],
)
def test_the_key_reaches_only_a_worker_that_can_use_it(
    model: str, tools: bool, expect_key: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, model)))
    if tools:
        monkeypatch.setenv("CARDS_RUNNER_USE_TOOLS", "1")
    else:
        monkeypatch.delenv("CARDS_RUNNER_USE_TOOLS", raising=False)
    assert ("ANTHROPIC_API_KEY" in _spawn_env(tmp_path, monkeypatch)) is expect_key


def test_an_unloadable_pinned_map_gets_no_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.delenv("CARDS_RUNNER_USE_TOOLS", raising=False)
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(tmp_path / "gone.yaml"))
    assert "ANTHROPIC_API_KEY" not in _spawn_env(tmp_path, monkeypatch)


# ---- (d) git config through the tool belt is read-only --------------------

@pytest.mark.parametrize(
    "args",
    [["--get", "user.name"], ["--list"], ["-l", "--show-origin"], ["--get-regexp", "^core"],
     ["--local", "--get", "core.bare"], ["get", "user.name"], ["list"]],
)
def test_git_config_read_forms_are_allowed(args: list[str]) -> None:
    assert _git_config_is_read_only(args)


@pytest.mark.parametrize(
    "args",
    [["core.hooksPath", "evil"], ["--add", "core.fsmonitor", "x"], ["--global", "credential.helper", "x"],
     ["--get", "x", "--add", "y", "z"], ["--unset", "core.x"], ["--replace-all", "a", "b"],
     ["set", "core.hooksPath", "x"], ["unset", "core.x"], ["edit"], ["--file=/tmp/x", "--get", "a"],
     ["--edit"], ["--rename-section", "a", "b"]],
)
def test_git_config_writes_are_refused(args: list[str]) -> None:
    assert not _git_config_is_read_only(args)


def test_the_git_tool_refuses_a_config_write(tmp_path: Path) -> None:
    belt = ToolBelt(worktree=tmp_path, env={})
    with pytest.raises(ToolError, match="read-only"):
        belt.execute("git", {"args": ["config", "core.hooksPath", "evil"]})


# ---- (e) tool mode + PR gate is refused ------------------------------------

def test_sdk_tools_with_the_pr_gate_is_refused(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from cards_runner.cli.__main__ import _cmd_start

    built: list[object] = []
    monkeypatch.setattr("cards_runner.cli.__main__.Daemon", lambda cfg: built.append(cfg))
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    args = argparse.Namespace(
        todo_root=Path("/tmp/td"), store="", poll_interval_sec=5.0, max_parallel=1,
        orphan_timeout_minutes=120, heartbeat_interval_sec=30.0, stub_sleep_sec=3.0,
        invoker="sdk-tools", skip_worktree=True, no_verifier=False, pr_gate=True,
        gh_path=None, git_path=None, auto_merge_strategy=None, pr_base_branch_default=None,
        no_boot_alive_check=False, forensic_ttl_hours=None, pr_unblock=False,
        sibling_reviewer=False, amendment_reviewer=False, worktree_prune=False,
        worktree_prune_interval_sec=None, project_config_path=None,
    )
    assert _cmd_start(args) == 2
    assert built == []
    assert "sdk-tools with --pr-gate is refused" in capsys.readouterr().err


class _Booted(Exception):
    pass


def test_an_ambient_use_tools_cannot_put_an_sdk_worker_in_tools_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # F5: `--invoker sdk --pr-gate` with CARDS_RUNNER_USE_TOOLS=1 in the
    # environment used to boot a tools-mode worker past the refusal.
    from cards_runner.cli.__main__ import _cmd_start

    monkeypatch.setenv("CARDS_RUNNER_USE_TOOLS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    # Positive control: the worker reads the ambient variable as tools mode.
    assert SdkInvoker.from_env().use_tools is True
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)  # from_env popped it

    seen: dict[str, object] = {}

    def boot(cfg: object) -> None:
        seen["use_tools"] = os.environ.get("CARDS_RUNNER_USE_TOOLS")
        raise _Booted

    monkeypatch.setattr("cards_runner.cli.__main__.Daemon", boot)
    monkeypatch.setattr("cards_runner.cli.__main__._pin_worker_env", lambda: None)
    monkeypatch.setattr("cards_runner.cli.__main__._tier_map_problem", lambda: None)
    args = argparse.Namespace(
        todo_root=tmp_path / "td", store="", poll_interval_sec=5.0, max_parallel=1,
        orphan_timeout_minutes=120, heartbeat_interval_sec=30.0, stub_sleep_sec=3.0,
        invoker="sdk", skip_worktree=True, no_verifier=False, pr_gate=True,
        gh_path=None, git_path=None, auto_merge_strategy=None, pr_base_branch_default=None,
        no_boot_alive_check=False, forensic_ttl_hours=None, pr_unblock=False,
        sibling_reviewer=False, amendment_reviewer=False, worktree_prune=False,
        worktree_prune_interval_sec=None, project_config_path=None,
    )
    with pytest.raises(_Booted):
        _cmd_start(args)
    assert seen["use_tools"] is None  # cleared before the daemon (and its spawner) existed
    assert SdkInvoker.from_env().use_tools is False


# ---- hardened git env ------------------------------------------------------

def test_hardened_git_env_drops_keys_and_pins_the_exec_config() -> None:
    base = {"PATH": "x", "SYSTEMROOT": "C:\\Windows", "ANTHROPIC_API_KEY": FAKE_KEY,
            "OPENAI_API_KEY": "o", "AWS_SECRET_ACCESS_KEY": "a", "GH_TOKEN": "g",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "C:/evil"}
    env = git_safety.hardened_git_env(base)
    assert FAKE_KEY not in env.values() and "o" not in env.values() and "a" not in env.values()
    assert env["GH_TOKEN"] == "g"
    assert "C:/evil" not in env.values()  # a caller's own GIT_CONFIG_* cannot sneak through
    pinned = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
              for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pinned["core.fsmonitor"] == "false"
    assert Path(pinned["core.hooksPath"]).is_dir()
    assert not any(Path(pinned["core.hooksPath"]).iterdir())
    no_gh = git_safety.hardened_git_env(base, keep_gh_credentials=False)
    assert "GH_TOKEN" not in no_gh


def test_a_planted_file_in_the_empty_hooks_dir_is_removed_before_use() -> None:
    first = git_safety.empty_hooks_dir()
    (first / "pre-push").write_text("#!/bin/sh\nexit 0\n")
    assert not any(git_safety.empty_hooks_dir().iterdir())


def test_the_empty_config_files_are_re_emptied_before_every_use() -> None:
    env = git_safety.hardened_git_env({"PATH": "x"})
    for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        Path(env[key]).write_text("[core]\n\tsshCommand = evil\n")
    env = git_safety.hardened_git_env({"PATH": "x"})
    for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        assert Path(env[key]).stat().st_size == 0


# ---- (f) a planted hook does not fire on the daemon's git push --------------

def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run([GIT, *args], cwd=cwd, capture_output=True, text=True, check=True)
    return out.stdout


def _repo_with_remote(tmp: Path) -> Path:
    remote = tmp / "remote.git"
    _git(tmp, "init", "--bare", "-b", "main", str(remote))
    work = tmp / "work"
    _git(tmp, "clone", str(remote), str(work))
    _git(work, "config", "user.email", "t@example.test")
    _git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("one\n")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-m", "one")
    _git(work, "push", "origin", "HEAD:main")
    _git(work, "checkout", "-b", "card/x")
    (work / "a.txt").write_text("two\n")
    _git(work, "commit", "-am", "two")
    return work


def _plant_hook(hooks_dir: Path, marker: Path) -> None:
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-push"
    hook.write_text(f"#!/bin/sh\necho fired > '{marker.as_posix()}'\nexit 0\n", newline="\n")
    hook.chmod(0o755)


@needs_git
@pytest.mark.parametrize("via", ["hooks_dir", "core_hooksPath"])
def test_a_planted_pre_push_hook_does_not_fire_on_the_daemon_push(via: str, tmp_path: Path) -> None:
    work = _repo_with_remote(tmp_path)
    marker = tmp_path / "hook-fired.txt"
    if via == "hooks_dir":
        _plant_hook(work / ".git" / "hooks", marker)
    else:
        _plant_hook(tmp_path / "evil-hooks", marker)
        _git(work, "config", "core.hooksPath", (tmp_path / "evil-hooks").as_posix())

    # Positive control: a plain push runs the plant on this host.
    _git(work, "push", "origin", "card/x:control-branch")
    assert marker.exists(), "control: the planted hook should fire on a plain push"
    marker.unlink()

    # Trusted WITH the plant in place (a repo first seen already rigged):
    # this isolates the env overrides from the baseline check.
    git_safety.trust_repo(git_safety.common_dir_of(work))

    result = SubprocessGhRunner(git_path=GIT).push(work, "card/x", set_upstream=False)
    assert result.ok, result
    assert not marker.exists(), "the daemon's push ran a planted hook"
    assert "card/x" in _git(tmp_path / "remote.git", "branch", "--list", "card/x")


# ---- (g) diff_stats does not run a textconv driver ---------------------------

@needs_git
def test_diff_stats_does_not_run_a_planted_textconv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _repo_with_remote(tmp_path)
    marker = tmp_path / "textconv-fired.txt"
    driver = tmp_path / "conv.sh"
    driver.write_text(f"#!/bin/sh\necho fired > '{marker.as_posix()}'\ncat \"$1\"\n", newline="\n")
    driver.chmod(0o755)
    (work / ".gitattributes").write_text("*.txt diff=evil\n")
    _git(work, "config", "diff.evil.textconv", f"sh '{driver.as_posix()}'")

    # Positive control: a patch-format diff runs the driver.
    _git(work, "diff", "main...card/x")
    assert marker.exists(), "control: textconv should run on a plain patch diff"
    marker.unlink()
    git_safety.trust_repo(git_safety.common_dir_of(work))  # isolate the flags + env

    stats = DiffStats.from_worktree(work, branch="card/x", base_branch="main", git_path=GIT)
    assert not marker.exists(), "diff_stats ran a planted textconv driver"
    # and the hardening did not blind the diff: the real change is counted
    assert stats.files == ("a.txt",)
    assert stats.lines_added == 1 and stats.lines_removed == 1


def test_diff_stats_passes_the_flags_and_a_keyless_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # (g) pinned regardless of what --numstat would do on its own.
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object) -> SimpleNamespace:
        seen["argv"], seen["env"] = argv, kw.get("env")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr("cards_runner.daemon.diff_stats.subprocess.run", fake_run)
    DiffStats.from_worktree(tmp_path, branch="b", base_branch="main")
    assert "--no-ext-diff" in seen["argv"] and "--no-textconv" in seen["argv"]
    assert isinstance(seen["env"], dict) and FAKE_KEY not in seen["env"].values()


# ---- the persisted trust baseline (F1, F2, F3, F7) -------------------------

def _common(work: Path) -> Path:
    common = git_safety.common_dir_of(work)
    assert common is not None
    return common


@needs_git
def test_the_fingerprint_changes_on_a_config_or_hook_plant_and_only_then(tmp_path: Path) -> None:
    work = _repo_with_remote(tmp_path)
    before = git_safety.fingerprint(_common(work))
    (work / "a.txt").write_text("ordinary edit\n")  # tracked-file work is not a change
    assert git_safety.fingerprint(_common(work)) == before
    _git(work, "config", "core.fsmonitor", "sh -c 'echo x'")
    after_config = git_safety.fingerprint(_common(work))
    assert after_config != before
    _plant_hook(work / ".git" / "hooks", tmp_path / "m.txt")
    assert git_safety.fingerprint(_common(work)) != after_config


def test_no_common_dir_outside_a_git_checkout(tmp_path: Path) -> None:
    assert git_safety.common_dir_of(tmp_path) is None
    assert git_safety.assert_trusted(tmp_path) is None


@needs_git
def test_the_daemons_own_push_u_does_not_trip_the_baseline(tmp_path: Path) -> None:
    # F7: `push -u` writes a [branch] section; that must not quarantine.
    work = _repo_with_remote(tmp_path)
    git_safety.trust_repo(_common(work))
    assert SubprocessGhRunner(git_path=GIT).push(work, "card/x", set_upstream=True).ok
    assert "[branch \"card/x\"]" in (work / ".git" / "config").read_text()  # it did write one
    git_safety.assert_trusted(work)  # and the repo is still trusted


@needs_git
def test_a_plant_quarantines_the_repo_and_it_stays_quarantined_until_re_trusted(tmp_path: Path) -> None:
    work = _repo_with_remote(tmp_path)
    git_safety.trust_repo(_common(work))
    _git(work, "config", "core.fsmonitor", "sh -c 'echo x'")
    result = SubprocessGhRunner(git_path=GIT).push(work, "card/x")
    assert not result.ok and "quarantined" in result.reason
    assert "card/x" not in _git(tmp_path / "remote.git", "branch", "--list", "card/x")

    # F3: reverting does not lift it, and nothing re-baselines silently.
    _git(work, "config", "--unset", "core.fsmonitor")
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work, first_sight_ok=True)
    git_safety.trust_repo(_common(work))  # the human step
    git_safety.assert_trusted(work)


@needs_git
def test_a_worktree_repointed_at_another_repo_is_refused(tmp_path: Path) -> None:
    work = _repo_with_remote(tmp_path)
    git_safety.trust_repo(_common(work))
    wt = tmp_path / "wt"
    _git(work, "worktree", "add", str(wt), "-b", "card/y")
    git_safety.assert_trusted(wt)  # control: the real worktree resolves to the trusted repo
    evil = tmp_path / "evil"
    _git(tmp_path, "init", str(evil))
    (wt / ".git").unlink()  # git marks it hidden; Windows refuses to overwrite that
    (wt / ".git").write_text(f"gitdir: {(evil / '.git').as_posix()}\n")
    with pytest.raises(git_safety.UntrustedRepo):
        git_safety.assert_trusted(wt)


@needs_git
@pytest.mark.parametrize("planted_after_trust", [False, True])
def test_worktree_prep_runs_no_planted_hook(planted_after_trust: bool, tmp_path: Path) -> None:
    # F1: the next card's `git worktree add` runs in the shared .git.
    from cards_runner.common.types import RuntimePaths
    from cards_runner.daemon.worktree import prepare_worktree

    work = _repo_with_remote(tmp_path)
    marker = tmp_path / "post-checkout-fired.txt"
    hooks = work / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\necho fired > '{marker.as_posix()}'\n", newline="\n")
    hook.chmod(0o755)
    _git(work, "worktree", "add", str(tmp_path / "control-wt"), "-b", "control")
    assert marker.exists(), "control: post-checkout should fire on a plain worktree add"
    marker.unlink()

    if planted_after_trust:
        hook.rename(hooks / "post-checkout.off")
        git_safety.trust_repo(_common(work))
        (hooks / "post-checkout.off").rename(hook)
    else:
        git_safety.trust_repo(_common(work))  # rigged at first sight: overrides alone

    paths = RuntimePaths.from_root(tmp_path / "todo")
    paths.ensure()
    kwargs = dict(paths=paths, project_dir=work, branch_name="card/z",
                  base_branch="main", worktree_path=tmp_path / "card-wt")
    if planted_after_trust:
        with pytest.raises(git_safety.RepoQuarantined):
            prepare_worktree(**kwargs)
    else:
        prepare_worktree(**kwargs)
    assert not marker.exists(), "worktree prep ran a planted hook"


@needs_git
def test_a_global_gitconfig_write_does_not_reach_the_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # F2: the tool shell can write ~/.gitconfig; the daemon never reads it.
    work = _repo_with_remote(tmp_path)
    evil = tmp_path / "evil.git"
    _git(tmp_path, "init", "--bare", str(evil))
    real_url = _git(work, "remote", "get-url", "origin").strip()
    home = tmp_path / "home"
    home.mkdir()
    _git(tmp_path, "config", "--file", str(home / ".gitconfig"),
         f"url.{evil.as_posix()}.insteadOf", real_url)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)

    _git(work, "push", "origin", "card/x:control-branch")
    assert "control-branch" in _git(evil, "branch", "--list"), "control: insteadOf should redirect"

    git_safety.trust_repo(_common(work))
    assert SubprocessGhRunner(git_path=GIT).push(work, "card/x", set_upstream=False).ok
    assert "card/x" in _git(tmp_path / "remote.git", "branch", "--list", "card/x")
    assert "card/x" not in _git(evil, "branch", "--list", "card/x")


@needs_git
def test_trust_repo_cli_lifts_a_quarantine(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from cards_runner.cli.__main__ import main

    work = _repo_with_remote(tmp_path)
    todo = tmp_path / "todo"
    git_safety.configure(git_safety.state_dir_for(todo))
    git_safety.trust_repo(_common(work))
    _git(work, "config", "core.sshCommand", "evil")
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work)
    assert main(["trust-repo", "--todo-root", str(todo), str(work)]) == 0
    assert "quarantine lifted" in capsys.readouterr().out
    git_safety.assert_trusted(work)


def test_the_credential_helper_list_is_reset_before_gh_is_pinned() -> None:
    env = git_safety.hardened_git_env({"PATH": "x"}, gh_path="C:/gh.exe")
    pairs = [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"])
             for i in range(int(env["GIT_CONFIG_COUNT"]))]
    helpers = [v for k, v in pairs if k == "credential.helper"]
    assert helpers[0] == ""  # drops every helper a config file named
    assert helpers[1:] == ['!"C:/gh.exe" auth git-credential']
    no_gh = git_safety.hardened_git_env({"PATH": "x"}, keep_gh_credentials=False)
    assert [no_gh[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(no_gh["GIT_CONFIG_COUNT"]))
            if no_gh[f"GIT_CONFIG_KEY_{i}"] == "credential.helper"] == [""]


@needs_git
def test_git_cannot_walk_up_from_a_worktree_whose_dot_git_was_deleted(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    _git(tmp_path, "init", str(parent))
    child = parent / "child"
    child.mkdir()
    # Positive control: plain git in a dir with no .git finds the parent repo.
    assert _git(child, "rev-parse", "--show-toplevel").strip()
    proc = git_safety.run_git([GIT, "rev-parse", "--show-toplevel"], cwd=child)
    assert proc.returncode != 0 and "not a git repository" in proc.stderr.lower()


@pytest.mark.parametrize("args", [
    ["config", "--global", "--get", "user.email"],
    ["config", "-f", "C:/elsewhere/secrets.ini", "--list"],
    ["config", "--file", "C:/elsewhere/secrets.ini", "--get", "a.b"],
    ["config", "--system", "--list"],
    ["config", "get", "--global", "user.email"],
    ["config", "list", "--file=C:/elsewhere/secrets.ini"],
])
def test_the_git_tool_reads_only_the_repos_own_config(args: list[str]) -> None:
    # F10: a read of ANOTHER config file is a way to read arbitrary files.
    assert _git_config_is_read_only(args[1:]) is False
    assert _git_config_is_read_only(["--get", "user.email"]) is True  # control
