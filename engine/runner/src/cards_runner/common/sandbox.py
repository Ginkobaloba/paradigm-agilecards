"""Where model-run code executes: the one seam a sandbox slots in behind.

Two things run code the model wrote or chose:

1. the **worker** (the executor and its tool shell), and
2. the verifier's **shell AC commands** (often model-written tests).

Both go through a `SandboxLauncher`. Today there is one implementation,
`LocalProcessLauncher`: a child in a Job Object (Windows) or its own
process group (POSIX), same user, same filesystem, same network. A
container or cloud launcher replaces it without touching the daemon or
the verifier (see C:\\dev\\_status\\WORKER_ISOLATION_OPTIONS_2026-10-28.md).

What every launcher must guarantee: when `run_command` returns, or after
`ManagedProcess.end_job()` for a spawned worker, nothing it started is
still running. Callers read and apply outputs only after that.

`LocalProcessLauncher` meets that for every process the child creates
itself, but NOT for one it asks another service to create: WMI
(`Win32_Process.Create`) and by extension the Task Scheduler or COM start
processes outside the job, and those survive `end_job` (#87 deep verify
L3-01). Only an OS-level launcher (container, VM) closes that; see
RUNNER_CONTRACT.md "What this does not isolate".
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from .process_group import ManagedProcess, spawn_in_job

# Cap on captured AC output per stream, so a runaway command cannot
# exhaust daemon memory. Verifier evidence keeps only a tail anyway.
OUTPUT_CAP_BYTES = 1 << 20


@dataclass(frozen=True)
class CommandResult:
    """One finished (or killed) command."""

    returncode: int | None  # None when it was killed on timeout
    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxLauncher(Protocol):
    """Runs model-chosen code. Implementations decide the boundary."""

    def spawn_worker(
        self, args: list[str], *, cwd: Path, env: dict[str, str],
        stdout: BinaryIO, stderr: BinaryIO,
    ) -> ManagedProcess: ...

    def run_command(
        self, args: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
    ) -> CommandResult: ...


def _read_capped(handle: BinaryIO) -> str:
    handle.seek(0)
    data = handle.read(OUTPUT_CAP_BYTES + 1)
    text = data[:OUTPUT_CAP_BYTES].decode("utf-8", errors="replace")
    # Text mode, as `subprocess.run(text=True)` gave the shell handler
    # before the launcher seam: an AC expecting "a\nb" passed on main and
    # failed on cmd.exe's "a\r\nb" (#87 deep verify L3-02).
    text = text.replace("\r\n", "\n")
    return text + ("\n[output truncated]" if len(data) > OUTPUT_CAP_BYTES else "")


class LocalProcessLauncher:
    """Same-user child processes, each in its own Job Object / process group."""

    def spawn_worker(
        self, args: list[str], *, cwd: Path, env: dict[str, str],
        stdout: BinaryIO, stderr: BinaryIO,
    ) -> ManagedProcess:
        return spawn_in_job(args, cwd=str(cwd), env=env,
                            stdout=stdout.fileno(), stderr=stderr.fileno())

    def run_command(
        self, args: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
    ) -> CommandResult:
        """Run to completion (or `timeout`), then end the whole job.

        `args[0]` is resolved against the DAEMON's PATH, because the Job
        Object spawn does not search PATH and `env` may be empty.
        """
        program = shutil.which(args[0]) or args[0]
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = spawn_in_job([program, *args[1:]], cwd=str(cwd), env=env,
                                   stdout=out.fileno(), stderr=err.fileno())
            timed_out = False
            try:
                returncode: int | None = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out, returncode = True, None
            finally:
                process.end_job()  # descendants die here, timed out or not
            if timed_out:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            return CommandResult(returncode, _read_capped(out), _read_capped(err), timed_out)


_DEFAULT: SandboxLauncher = LocalProcessLauncher()


def default_launcher() -> SandboxLauncher:
    return _DEFAULT


__all__ = ["CommandResult", "LocalProcessLauncher", "SandboxLauncher", "default_launcher",
           "OUTPUT_CAP_BYTES"]
