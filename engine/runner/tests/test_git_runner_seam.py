"""Every daemon-side git/gh call goes through `git_safety.run_git`.

Two pins (#85 r2, F1): a source scan that no module outside a short,
reasoned allowlist launches a process at all, and a runtime check that
the git/gh calls the daemon does make all carry the hardened env.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cards_runner.common import git_safety
from cards_runner.daemon.diff_stats import DiffStats
from cards_runner.daemon.pr_lifecycle import SubprocessGhRunner
from cards_runner.daemon.worktree import prune_git_worktrees

SRC = Path(__file__).resolve().parents[1] / "src" / "cards_runner"

# Process launches allowed outside run_git, each with the reason it is not
# a daemon git/gh call in an executor-written repo.
ALLOWED = {
    "common/git_safety.py": "the hardened runner itself",
    "common/process_group.py": "launches the worker (spawn_in_job)",
    "store/dolt_store.py": "runs the dolt binary on the card store",
    "verifier/handlers/deterministic.py": "runs card AC commands with env={}",
    "cli/doctor.py": "`<binary> --version` only, no repo",
}
LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output", "system", "popen",
             "create_subprocess_exec", "create_subprocess_shell"}


def _launches(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (node.func.attr in LAUNCHERS and isinstance(owner, ast.Name)
                    and owner.id in {"subprocess", "os", "asyncio"}):
                found.append(node.lineno)
    return found


def test_no_daemon_module_launches_a_process_outside_the_allowlist() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("worker_stub/") or rel in ALLOWED:
            continue  # the worker's own tools are the executor's, not the daemon's
        offenders += [f"{rel}:{line}" for line in _launches(path)]
    assert offenders == [], f"route these through git_safety.run_git: {offenders}"


def test_the_scan_catches_a_bare_subprocess_run(tmp_path: Path) -> None:
    # control: the scanner is not blind
    probe = tmp_path / "probe.py"
    probe.write_text("import subprocess\nsubprocess.run(['git', 'push'])\n")
    assert _launches(probe) == [2]


def test_every_daemon_git_and_gh_call_carries_the_hardened_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_safety.configure(tmp_path / "state")
    (tmp_path / ".git").mkdir()  # a repo the daemon trusts at first sight
    git_safety.assert_trusted(tmp_path, first_sight_ok=True)
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kw: Any) -> SimpleNamespace:
        calls.append({"argv": argv, **kw})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    gh = SubprocessGhRunner()
    gh.push(tmp_path, "card/x")
    gh.open_pr(tmp_path, title="t", body="b", base="main")
    DiffStats.from_worktree(tmp_path, branch="card/x", base_branch="main")
    prune_git_worktrees(project_dir=tmp_path)

    assert len(calls) == 4
    for call in calls:
        env = call["env"]
        assert isinstance(env, dict), call["argv"]
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull, call["argv"]
        assert env["GIT_CONFIG_SYSTEM"] == os.devnull, call["argv"]
        assert int(env["GIT_CONFIG_COUNT"]) >= 6, call["argv"]
        assert "GIT_CEILING_DIRECTORIES" in env, call["argv"]


def test_daemon_git_is_the_binary_pinned_at_boot_not_a_later_path_plant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    git_safety.configure(tmp_path / "state")  # daemon boot
    pinned = git_safety.resolved("git")
    assert Path(pinned).is_absolute()
    plant_dir = tmp_path / "bin"
    plant_dir.mkdir()
    (plant_dir / "git.bat").write_text("@echo planted\n")
    (plant_dir / "git").write_text("#!/bin/sh\necho planted\n")
    (plant_dir / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(plant_dir) + os.pathsep + os.environ["PATH"])
    # Positive control: after the plant, a PATH lookup finds it.
    assert Path(shutil.which("git") or "").parent == plant_dir

    calls: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: (calls.append(argv),
                        SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
    git_safety.run_git(["git", "status"], cwd=None)
    assert calls == [[pinned, "status"]]
