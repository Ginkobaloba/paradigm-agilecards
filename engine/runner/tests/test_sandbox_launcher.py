"""The sandbox seam: nothing model-run outlives the call that started it.

Model-run code can start background processes. Each test first shows the
escape working (positive control), then shows the launcher contains it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cards_runner.common import process_group
from cards_runner.common.locks import pid_alive
from cards_runner.common.process_group import spawn_in_job
from cards_runner.common.sandbox import CommandResult, LocalProcessLauncher
from cards_runner.verifier.handlers.deterministic import HandlerContext, handle_shell

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Job Object semantics")
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _parent_script(tmp: Path, *, breakaway: bool = False) -> Path:
    """A parent that starts a long-lived child, records its pid, and exits."""
    child = tmp / "child.py"
    child.write_text("import time\ntime.sleep(60)\n")
    pidfile = tmp / "child.pid"
    flags = CREATE_BREAKAWAY_FROM_JOB if breakaway else 0
    parent = tmp / "parent.py"
    parent.write_text(
        "import subprocess, sys\n"
        "try:\n"
        f"    p = subprocess.Popen([sys.executable, r'{child}'], creationflags={flags})\n"
        f"    open(r'{pidfile}', 'w').write(str(p.pid))\n"
        "except OSError as exc:\n"
        f"    open(r'{pidfile}', 'w').write('denied')\n"
    )
    return parent


def _child_pid(tmp: Path) -> int | None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            text = (tmp / "child.pid").read_text().strip()
        except OSError:
            text = ""
        if text:
            return None if text == "denied" else int(text)
        time.sleep(0.05)
    raise AssertionError("parent never reported")


def _gone(pid: int, wait: float = 5.0) -> bool:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return False


def _kill(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/PID", str(pid)] if sys.platform == "win32"
                   else ["kill", "-9", str(pid)], capture_output=True)


def test_a_background_child_does_not_outlive_run_command(tmp_path: Path) -> None:
    parent = _parent_script(tmp_path)
    # Positive control: a plain subprocess.run leaves the child running.
    subprocess.run([sys.executable, str(parent)], check=True)
    leaked = _child_pid(tmp_path)
    assert leaked is not None and pid_alive(leaked), "control: the child should survive"
    _kill(leaked)
    (tmp_path / "child.pid").unlink()

    result = LocalProcessLauncher().run_command(
        [sys.executable, str(parent)], cwd=tmp_path, env=dict(os.environ), timeout=30)
    assert result.returncode == 0 and not result.timed_out
    pid = _child_pid(tmp_path)
    assert pid is not None and _gone(pid), "a child outlived run_command"


def test_a_timeout_kills_the_command_and_its_children(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(60)\n")
    started = time.monotonic()
    result = LocalProcessLauncher().run_command(
        [sys.executable, str(script)], cwd=tmp_path, env=dict(os.environ), timeout=1)
    assert result.timed_out and result.returncode is None
    assert time.monotonic() - started < 20


def test_output_is_captured_and_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cards_runner.common.sandbox.OUTPUT_CAP_BYTES", 100)
    script = tmp_path / "loud.py"
    script.write_text("import sys\nprint('x' * 5000)\nsys.stderr.write('err-0042')\n")
    result = LocalProcessLauncher().run_command(
        [sys.executable, str(script)], cwd=tmp_path, env=dict(os.environ), timeout=30)
    assert result.stdout.endswith("[output truncated]") and len(result.stdout) < 200
    assert "err-0042" in result.stderr


def test_end_job_kills_what_kill_tree_leaves_after_the_worker_exits(tmp_path: Path) -> None:
    parent = _parent_script(tmp_path)
    proc = spawn_in_job([sys.executable, str(parent)], cwd=str(tmp_path), env=dict(os.environ))
    proc.wait(timeout=30)
    pid = _child_pid(tmp_path)
    assert pid is not None
    proc.kill_tree()  # control: returns early because the worker already exited
    assert pid_alive(pid), "control: kill_tree should leave the orphan running"
    proc.end_job()
    assert _gone(pid), "end_job left a descendant running"


@windows_only
@pytest.mark.parametrize("breakaway_allowed", [True, False])
def test_a_descendant_cannot_break_away_from_the_job(
    breakaway_allowed: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if breakaway_allowed:  # positive control: the old flags let it escape
        monkeypatch.setattr(process_group, "JOB_LIMIT_FLAGS",
                            process_group.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                            | process_group.JOB_OBJECT_LIMIT_BREAKAWAY_OK)
    parent = _parent_script(tmp_path, breakaway=True)
    LocalProcessLauncher().run_command(
        [sys.executable, str(parent)], cwd=tmp_path, env=dict(os.environ), timeout=30)
    pid = _child_pid(tmp_path)
    if breakaway_allowed:
        assert pid is not None and pid_alive(pid), "control: breakaway should escape"
        _kill(pid)
    else:
        assert pid is None or _gone(pid), "a descendant broke away from the job"


def test_the_shell_ac_handler_runs_through_the_launcher(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class Fake:
        def run_command(self, args, *, cwd, env, timeout):  # noqa: ANN001
            calls.append(args)
            return CommandResult(0, "ok", "")

        def spawn_worker(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("not used")

    ctx = HandlerContext(worktree=tmp_path, env={}, launcher=Fake())
    out = handle_shell({"command": "echo hi"}, ctx)
    assert out.passed and calls and calls[0][-1] == "echo hi"
