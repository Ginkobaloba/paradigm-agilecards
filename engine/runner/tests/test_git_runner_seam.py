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
    "common/sandbox.py": "the sandbox seam: worker spawn and verifier shell ACs",
    "cli/doctor.py": "`<binary> --version` only, no repo",
    "common/locks.py": "ctypes OpenProcess/GetExitCodeProcess for PID liveness only",
}
# What `subprocess` may be used for outside the allowlist: its exception
# and constant names, never a launcher.
SAFE_SUBPROCESS = {"CalledProcessError", "TimeoutExpired", "SubprocessError",
                   "CompletedProcess", "PIPE", "DEVNULL", "STDOUT"}
OS_LAUNCHERS = {"system", "popen", "startfile", "fork", "forkpty", "posix_spawn", "posix_spawnp"}
OS_LAUNCHER_PREFIXES = ("spawn", "exec")
ASYNCIO_LAUNCHERS = {"create_subprocess_exec", "create_subprocess_shell", "subprocess"}
# Importing any of these at all is a launch path.
FORBIDDEN_MODULES = {"multiprocessing", "ctypes", "_winapi", "pty", "pexpect", "asyncio.subprocess"}
LAUNCH_NAMES = ({"run", "Popen", "call", "check_call", "check_output", "getoutput",
                 "getstatusoutput"} | OS_LAUNCHERS | ASYNCIO_LAUNCHERS)


def _is_os_launcher(name: str) -> bool:
    return name in OS_LAUNCHERS or name.startswith(OS_LAUNCHER_PREFIXES)


def _launches(path: Path) -> list[int]:
    """Lines that launch, or could launch, a process. Errs toward flagging."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}  # local name -> "subprocess" | "os" | "asyncio"
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if a.name in FORBIDDEN_MODULES or root in FORBIDDEN_MODULES:
                    found.add(node.lineno)
                if a.name in ("subprocess", "os", "asyncio"):
                    aliases[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in FORBIDDEN_MODULES or mod.split(".")[0] in FORBIDDEN_MODULES:
                found.add(node.lineno)
            for a in node.names:
                if ((mod == "subprocess" and a.name not in SAFE_SUBPROCESS)
                        or (mod == "os" and (_is_os_launcher(a.name) or a.name == "*"))
                        or (mod == "asyncio" and a.name in ASYNCIO_LAUNCHERS | {"*"})):
                    found.add(node.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            kind = aliases.get(node.value.id)
            if ((kind == "subprocess" and node.attr not in SAFE_SUBPROCESS)
                    or (kind == "os" and _is_os_launcher(node.attr))
                    or (kind == "asyncio" and node.attr in ASYNCIO_LAUNCHERS)):
                found.add(node.lineno)
        elif isinstance(node, ast.Name) and aliases.get(node.id) == "subprocess":
            # the module object itself, not an attribute of it (getattr, aliasing)
            parent_is_attr = any(isinstance(p, ast.Attribute) and p.value is node
                                 for p in ast.walk(tree))
            if not parent_is_attr:
                found.add(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn, args = node.func.id, node.args
            if fn == "getattr" and len(args) >= 2:
                attr, owner = args[1], args[0]
                dynamic_on_module = (not isinstance(attr, ast.Constant)
                                     and isinstance(owner, ast.Name) and owner.id in aliases)
                literal = attr.value if isinstance(attr, ast.Constant) else None
                if dynamic_on_module or (isinstance(literal, str) and (
                        literal in LAUNCH_NAMES or _is_os_launcher(literal))):
                    found.add(node.lineno)
            elif fn in {"__import__", "eval", "exec"}:
                found.add(node.lineno)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"):
            found.add(node.lineno)
    return sorted(found)


def test_no_daemon_module_launches_a_process_outside_the_allowlist() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("worker_stub/") or rel in ALLOWED:
            continue  # the worker's own tools are the executor's, not the daemon's
        offenders += [f"{rel}:{line}" for line in _launches(path)]
    assert offenders == [], f"route these through git_safety.run_git: {offenders}"


@pytest.mark.parametrize("source", [
    "import subprocess\nsubprocess.run(['git', 'push'])\n",
    "from subprocess import run\nrun(['git'])\n",
    "from subprocess import run as r\nr(['git'])\n",
    "import subprocess as sp\nsp.Popen(['git'])\n",
    "import subprocess\nf = getattr(subprocess, 'run')\n",
    "import subprocess\nname = 'run'\nf = getattr(subprocess, name)\n",
    "import subprocess\nmod = subprocess\n",
    "import os\nos.system('git push')\n",
    "import os as o\no.spawnv(0, 'git', ['git'])\n",
    "import os\nos.execv('git', ['git'])\n",
    "from os import startfile\nstartfile('x')\n",
    "import os\nos.posix_spawn('git', ['git'], {})\n",
    "import asyncio\nasyncio.create_subprocess_exec('git')\n",
    "import importlib\nimportlib.import_module('subprocess')\n",
    "m = __import__('subprocess')\n",
    "import multiprocessing\n",
    "import ctypes\n",
])
def test_the_scan_catches_every_launch_form(source: str, tmp_path: Path) -> None:
    # control: the scanner is not blind to any of these shapes
    probe = tmp_path / "probe.py"
    probe.write_text(source)
    assert _launches(probe), source


def test_the_scan_allows_the_exception_names() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        probe = Path(d) / "ok.py"
        probe.write_text("import subprocess\nimport os\n"
                         "try:\n    pass\nexcept subprocess.CalledProcessError:\n    pass\n"
                         "x = os.environ.get('A')\n")
        assert _launches(probe) == []


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
