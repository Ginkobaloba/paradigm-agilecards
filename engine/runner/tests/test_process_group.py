"""process_group: the spawn-in-job lifecycle.

Exercises `spawn_in_job` end to end on the host platform. On Windows
this drives the chunk 2b-ii `CREATE_SUSPENDED` -> assign-to-job ->
`ResumeThread` path and the `_Win32Process` facade; on POSIX it
drives the process-group path. Both must start a child, propagate
its exit code, capture its stdout, and tree-kill it on demand.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from cards_runner.common.process_group import ManagedProcess, spawn_in_job


def _spawn(code: str, **kwargs: object) -> ManagedProcess:
    return spawn_in_job(
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        env=dict(os.environ),
        **kwargs,  # type: ignore[arg-type]
    )


def test_child_exit_code_propagates() -> None:
    proc = _spawn("import sys; sys.exit(7)")
    assert isinstance(proc.pid, int) and proc.pid > 0
    rc = proc.wait(timeout=30)
    assert rc == 7
    assert proc.poll() == 7


def test_clean_child_exits_zero() -> None:
    proc = _spawn("x = 1 + 1")
    assert proc.wait(timeout=30) == 0


def test_stdout_is_captured_to_a_file(tmp_path: Path) -> None:
    out_path = tmp_path / "child.log"
    handle = open(out_path, "wb")
    try:
        proc = spawn_in_job(
            [sys.executable, "-c", "print('hello-from-worker')"],
            cwd=str(tmp_path),
            env=dict(os.environ),
            stdout=handle.fileno(),
            stderr=handle.fileno(),
        )
    finally:
        # The child holds its own inherited copy of the handle.
        handle.close()
    proc.wait(timeout=30)
    assert "hello-from-worker" in out_path.read_text(encoding="utf-8")


def test_kill_tree_terminates_a_running_child() -> None:
    proc = _spawn("import time; time.sleep(60)")
    time.sleep(0.5)
    assert proc.poll() is None  # still running.
    proc.kill_tree(grace_sec=2.0)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    assert proc.poll() is not None  # the kill landed.
