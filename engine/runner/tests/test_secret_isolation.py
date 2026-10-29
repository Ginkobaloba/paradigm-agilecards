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
    monkeypatch.setattr("cards_runner.common.sandbox.spawn_in_job", lambda args, *, env, **_: seen.update(env) or object())
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


def test_user_and_system_config_are_the_null_device() -> None:
    env = git_safety.hardened_git_env({"PATH": "x", "GIT_CONFIG_GLOBAL": "C:/evil.cfg"})
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_SYSTEM"] == os.devnull


@needs_git
def test_a_deleted_trust_store_cannot_re_arm_first_sight(tmp_path: Path, _git_state: Path) -> None:
    # The store sits at a fixed path near every worktree; a running daemon's
    # in-memory records are what count.
    work = tmp_path / "w"
    _git(tmp_path, "init", str(work))
    git_safety.trust_repo(_common(work))
    (work / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\n")
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work)
    (_git_state / "git_trust.json").unlink()
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work, first_sight_ok=True)
    # Control: a process with no memory of the repo (a restart) re-baselines.
    git_safety.configure(_git_state)
    assert git_safety.assert_trusted(work, first_sight_ok=True) == _common(work)


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


@needs_git
def test_diff_stats_raises_on_a_quarantined_repo_instead_of_reporting_no_diff(tmp_path: Path) -> None:
    # An empty diff would read as "nothing sensitive changed" to the gate.
    work = _repo_with_remote(tmp_path)
    git_safety.trust_repo(_common(work))
    stats = DiffStats.from_worktree(work, branch="card/x", base_branch="main", git_path=GIT)
    assert stats.files == ("a.txt",)  # control: a trusted repo diffs normally
    _git(work, "config", "core.sshCommand", "evil")
    with pytest.raises(git_safety.RepoQuarantined):
        DiffStats.from_worktree(work, branch="card/x", base_branch="main", git_path=GIT)


# ---- r3: the hash sees what git sees (N1), worktree configs (R18), push creds (R30)


def _plant_raw(path: Path, payload: bytes) -> None:
    path.write_bytes(path.read_bytes() + payload)


@needs_git
def test_a_lone_cr_cannot_hide_an_exec_key_from_the_hash(tmp_path: Path) -> None:
    # N1: `splitlines()` split on a lone CR and git did not, so a fake
    # `[branch]` header hid every key after it from the hash.
    work = _repo_with_remote(tmp_path)
    cfg = work / ".git" / "config"
    _plant_raw(cfg, b"[core]\n\tnote = x\n")  # a free-form section, last, and trusted
    git_safety.trust_repo(_common(work))
    # The plant only EXTENDS the trusted last line: to a line-splitting
    # reader the old lines are unchanged and the rest is a [branch] block.
    cfg.write_bytes(cfg.read_bytes().rstrip(b"\n") + b'\r[branch "z"]\n\tsshCommand = evil-n1-0042\n')
    # Positive control: git honours the hidden key.
    got = subprocess.run([GIT, "config", "--file", str(work / ".git" / "config"), "--get",
                          "core.sshCommand"], capture_output=True, text=True)
    assert got.stdout.strip() == "evil-n1-0042"
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work)


@needs_git
@pytest.mark.parametrize("payload", [
    b'[core]\n\tnote = x\r[branch "z"]\n\tsshCommand = evil\n',
    b'[core]\n\tsshCommand = "ev\\\n il"\n',             # continuation inside quotes
    b'[core] sshCommand = evil ; comment\n',              # key on the header line
    b'[CoRe]\n\tSSHCOMMAND = evil\n',                     # case
    b'[remote "a.b"]\n\turl = x\n\tpushurl = y\n',      # dotted subsection
    b'[include]\n\tpath = elsewhere.cfg\n',               # include key itself
    b'[branch "x"]\n\tremote = origin\n\tpushRemote = evil\n',  # non-daemon branch key
])
def test_the_hashed_entries_are_exactly_what_git_reads(payload: bytes, tmp_path: Path) -> None:
    # Differential: what the hash covers == what git itself lists, minus
    # only the daemon-written branch.<name>.remote/merge keys.
    work = _repo_with_remote(tmp_path)
    _plant_raw(work / ".git" / "config", payload)
    proc = subprocess.run([GIT, "config", "--local", "--includes", "--null", "--list"], cwd=work,
                          capture_output=True)
    git_sees = []
    for r in proc.stdout.split(b"\0"):
        if r:
            key, sep, value = r.decode().partition("\n")
            git_sees.append((key, value if sep else None))
    hashed = git_safety.config_entries(work / ".git" / "config")
    assert hashed is not None
    remotes = git_safety._remote_names(git_sees)

    def kept(entries: list) -> list:
        return [e for e in entries if not git_safety._is_daemon_written(e[0], e[1], remotes)]

    expected = kept(git_sees)
    assert kept(hashed) == expected
    if b"pushRemote" in payload:  # only remote/merge are exempt, never pushRemote
        assert ("branch.x.pushremote", "evil") in expected


@needs_git
def test_a_write_to_a_worktree_config_quarantines(tmp_path: Path) -> None:
    # R18: with extensions.worktreeConfig, git reads per-worktree config.
    work = _repo_with_remote(tmp_path)
    _git(work, "config", "extensions.worktreeConfig", "true")
    wt = tmp_path / "wt18"
    _git(work, "worktree", "add", str(wt), "-b", "card/r18")
    git_safety.trust_repo(_common(work))  # trusted with the extension already on
    _git(wt, "config", "--worktree", "core.sshCommand", "evil-r18")
    assert _git(wt, "config", "--get", "core.sshCommand").strip() == "evil-r18"  # control
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(wt)


@needs_git
def test_a_write_to_the_main_config_worktree_quarantines(tmp_path: Path) -> None:
    work = _repo_with_remote(tmp_path)
    git_safety.trust_repo(_common(work))
    (work / ".git" / "config.worktree").write_text("[core]\n\tsshCommand = evil\n")
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work)


def test_the_daemon_push_keeps_its_github_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # R30: the hardening must not strip what an authenticated push needs.
    monkeypatch.setenv("GH_TOKEN", "gh-push-0042")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object) -> SimpleNamespace:
        seen["env"] = kw.get("env")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("cards_runner.daemon.pr_lifecycle.shutil.which", lambda name: "C:/gh/gh.exe")
    assert SubprocessGhRunner().push(tmp_path, "card/x").ok
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "gh-push-0042"
    assert FAKE_KEY not in env.values()
    helpers = [env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))
               if env[f"GIT_CONFIG_KEY_{i}"] == "credential.helper"]
    assert helpers == ["", '!"C:/gh/gh.exe" auth git-credential']


@needs_git
def test_the_tool_shell_does_not_read_the_system_git_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The system config names Git for Windows' credential helper (GCM),
    # through which `git credential fill` returns stored passwords.
    system_cfg = tmp_path / "system.gitconfig"
    system_cfg.write_text("[credential]\n\thelper = !echo password=leak-0042 #\n")
    from cards_runner.common.env_scrub import scrub_environment

    plain = scrub_environment(base=dict(os.environ))
    plain["GIT_CONFIG_SYSTEM"] = str(system_cfg)
    control = subprocess.run([GIT, "config", "--list", "--show-scope"], cwd=tmp_path,
                             capture_output=True, text=True, env=plain)
    assert "system\tcredential.helper" in control.stdout  # control: the system scope is read

    worktree = tmp_path / "run" / "worktree"
    worktree.mkdir(parents=True)
    belt = SdkInvoker(api_key=FAKE_KEY)._build_tool_belt(
        SimpleNamespace(worktree=str(worktree)))  # type: ignore[arg-type]
    assert belt.env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert belt.env["GCM_INTERACTIVE"] == "never"
    # Even with a system file named, NOSYSTEM wins: nothing from it is read.
    probe = dict(belt.env, GIT_CONFIG_SYSTEM=str(system_cfg))
    out = subprocess.run([GIT, "config", "--list", "--show-scope"], cwd=worktree,
                         capture_output=True, text=True, env=probe)
    assert "leak-0042" not in out.stdout and "system\t" not in out.stdout


# ---- follow-up to #85 r3: W1, W2, M03/M06/M07/M11 gaps, include targets (M15)


@needs_git
def test_a_branch_remote_that_is_a_path_is_hashed(tmp_path: Path) -> None:
    # W1: `branch.<x>.remote` is exempt only when it NAMES a configured remote.
    work = _repo_with_remote(tmp_path)
    git_safety.trust_repo(_common(work))
    _git(work, "config", "branch.card/x.remote", "origin")  # what push -u writes
    git_safety.assert_trusted(work)  # control: a remote NAME stays exempt
    evil = tmp_path / "evil-w1.git"
    _git(tmp_path, "init", "--bare", str(evil))
    _git(work, "config", "branch.card/x.remote", evil.as_posix())
    # Positive control: a remote-less push honours the path.
    pushed = subprocess.run([GIT, "-c", "push.default=current", "push"], cwd=work,
                            capture_output=True, text=True)
    assert pushed.returncode == 0 and "card/x" in _git(evil, "branch", "--list")
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work)


@needs_git
def test_a_bare_key_and_an_empty_value_hash_differently(tmp_path: Path) -> None:
    # W2: `[http] sslVerify` is true; `sslVerify =` is false.
    work = _repo_with_remote(tmp_path)
    cfg = work / ".git" / "config"
    original = cfg.read_bytes()
    _plant_raw(cfg, b"[http]\n\tsslVerify\n")
    bare = git_safety.fingerprint(_common(work))
    cfg.write_bytes(original + b"[http]\n\tsslVerify =\n")
    empty = git_safety.fingerprint(_common(work))
    # Positive control: git reads them as different booleans.
    assert _git(work, "config", "--type=bool", "--get", "http.sslVerify").strip() == "false"
    assert bare != empty


@needs_git
def test_editing_a_value_in_place_quarantines(tmp_path: Path) -> None:
    # M06: a value edit with no new key must change the hash.
    work = _repo_with_remote(tmp_path)
    _git(work, "config", "core.sshCommand", "ssh -o BatchMode=yes")
    git_safety.trust_repo(_common(work))
    _git(work, "config", "core.sshCommand", "sh evil-m06")
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work)


@needs_git
def test_an_unparsable_config_is_hashed_as_bytes(tmp_path: Path) -> None:
    # M03/M07: if git cannot parse the file, every byte edit still counts.
    work = _repo_with_remote(tmp_path)
    cfg = work / ".git" / "config"
    cfg.write_bytes(cfg.read_bytes() + b"[broken\n\tsshCommand = a\n")
    assert git_safety.config_entries(cfg) is None  # control: git really refuses it
    git_safety.trust_repo(_common(work))
    cfg.write_bytes(cfg.read_bytes().replace(b"sshCommand = a", b"sshCommand = b"))
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work)


@needs_git
def test_editing_an_include_target_quarantines(tmp_path: Path) -> None:
    # Closes the declared include-target residual (r3 M15).
    work = _repo_with_remote(tmp_path)
    target = tmp_path / "shared.gitconfig"
    target.write_text("[core]\n\tnote = team\n")
    _git(work, "config", "include.path", target.as_posix())
    git_safety.trust_repo(_common(work))
    git_safety.assert_trusted(work)  # control: an unchanged target stays trusted
    target.write_text("[core]\n\tnote = team\n\tsshCommand = evil-inc\n")
    assert _git(work, "config", "--get", "core.sshCommand").strip() == "evil-inc"  # git honours it
    with pytest.raises(git_safety.RepoQuarantined):
        git_safety.assert_trusted(work)


@needs_git
def test_no_credential_or_exec_key_is_carried_into_the_tool_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # M11: only the listed, non-executable settings cross into the throwaway config.
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    real_cfg = real_home / ".gitconfig"
    for key, value in (("user.name", "Owner"), ("user.email", "owner@example.test"),
                       ("core.autocrlf", "true"), ("credential.helper", "!echo password=leak"),
                       ("core.sshCommand", "evil"), ("core.hooksPath", "C:/evil"),
                       ("core.fsmonitor", "evil"), ("url.C:/evil.insteadOf", "https://x.invalid/")):
        _git(tmp_path, "config", "--file", str(real_cfg), key, value)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(real_home))
    for var in ("GIT_CONFIG_GLOBAL", "XDG_CONFIG_HOME", "HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(var, raising=False)
    worktree = tmp_path / "run" / "worktree"
    worktree.mkdir(parents=True)
    SdkInvoker(api_key=FAKE_KEY)._build_tool_belt(SimpleNamespace(worktree=str(worktree)))  # type: ignore[arg-type]
    carried = _git(tmp_path, "config", "--file", str(tmp_path / "run" / "tool-home" / ".gitconfig"),
                   "--list", "--name-only").split()
    assert "user.email" in carried and "core.autocrlf" in carried  # control: carrying works
    assert not [k for k in carried if k.split(".")[0] in {"credential", "url"}
                or k in {"core.sshcommand", "core.hookspath", "core.fsmonitor"}]
