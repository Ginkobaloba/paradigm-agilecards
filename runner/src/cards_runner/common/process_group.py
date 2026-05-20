"""Per-worker Job Object on Windows.

Per the architectural override on Fork 2, each worker subprocess is
wrapped in a Windows Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` so closing the job handle kills
the entire process tree, including any child processes the worker
spawned (the SDK's HTTPS workers, git, tool subprocesses).
`taskkill /T` is unreliable; this is the documented Win32 path.

**Chunk 2b-ii refinement.** Chunk 1 created the worker with
`subprocess.Popen` and called `AssignProcessToJobObject` immediately
after. That left a microsecond race: a descendant spawned between
`CreateProcess` and the assignment escapes the job. The chunk-1
comment named the fix and deferred it -- `subprocess.Popen` does not
expose the worker's main-thread handle, so it cannot create the
process suspended and resume it after assignment. Chunk 2b-ii drops
to `_winapi.CreateProcess` directly: the worker is created
`CREATE_SUSPENDED`, assigned to the job while frozen, and only then
resumed. No descendant can run -- let alone spawn -- before the job
owns the process. This matters now because the real executor imports
the Anthropic SDK, which spins up HTTPS connection workers on import.

On non-Windows hosts this module degrades to a no-op wrapper (POSIX
process groups already give tree-kill via `os.killpg`).

References:
- AssignProcessToJobObject:
  https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
- CREATE_SUSPENDED / ResumeThread:
  https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Protocol


if sys.platform == "win32":  # pragma: no cover - exercised only on Windows.
    import _winapi
    import msvcrt


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JobObjectExtendedLimitInformation = 9

# Win32 process-creation flags (literals: not all are exposed by
# `_winapi` on every CPython build).
_CREATE_SUSPENDED = 0x00000004
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_UNICODE_ENVIRONMENT = 0x00000400

# WaitForSingleObject return values.
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102


class _ProcessLike(Protocol):
    """The slice of `subprocess.Popen` that `ManagedProcess` relies on.

    POSIX uses a real `subprocess.Popen`; Windows uses `_Win32Process`.
    Both satisfy this structurally.
    """

    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = ...) -> int: ...

    def terminate(self) -> None: ...


@dataclass
class ManagedProcess:
    """A worker process plus its kill handle.

    On Windows the kill handle is a Job Object. Closing it terminates
    the entire process tree. On POSIX the same `kill_tree()` call
    sends SIGTERM (then SIGKILL after a grace period) to the process
    group.
    """

    popen: _ProcessLike
    _job_handle: Any = None  # Win32 job HANDLE (int) or None
    _on_posix: bool = False

    @property
    def pid(self) -> int:
        return self.popen.pid

    def poll(self) -> int | None:
        return self.popen.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self.popen.wait(timeout=timeout)

    def kill_tree(self, grace_sec: float = 5.0) -> None:
        """Kill the worker and any descendants.

        Windows: close the Job Object handle. The kernel terminates
        the entire job (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).

        POSIX: SIGTERM the process group, wait `grace_sec`, then SIGKILL.
        """
        if self.popen.poll() is not None:
            return
        if sys.platform == "win32" and self._job_handle is not None:
            try:
                # `_winapi.CloseHandle` takes the full handle value
                # without the truncation risk a bare ctypes call has.
                _winapi.CloseHandle(self._job_handle)
            except OSError:
                # Fall back to TerminateProcess on the worker only.
                try:
                    self.popen.terminate()
                except OSError:
                    pass
            self._job_handle = None
            return
        # POSIX path.
        try:
            os.killpg(os.getpgid(self.popen.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return
        try:
            self.popen.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self.popen.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass


class _Win32Process:
    """A `subprocess.Popen`-compatible facade over a raw process handle.

    `_winapi.CreateProcess` hands back a bare process handle as a
    plain int (this CPython build exposes no `_winapi.Handle` type).
    This wraps it so the daemon's `poll()` / `wait()` / `terminate()`
    calls work uniformly with the POSIX `subprocess.Popen` path, and
    closes the handle on garbage collection so it does not leak.
    """

    def __init__(self, handle: int, pid: int) -> None:
        self._handle: int | None = handle
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if self._handle is None:
            return self.returncode
        # `WaitForSingleObject(0)` is the unambiguous liveness probe:
        # `GetExitCodeProcess` alone cannot tell a still-running
        # process from one that legitimately exited with 259
        # (STILL_ACTIVE).
        if _winapi.WaitForSingleObject(self._handle, 0) != _WAIT_OBJECT_0:
            return None
        self.returncode = _winapi.GetExitCodeProcess(self._handle)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        if self._handle is None:  # pragma: no cover - defensive.
            raise RuntimeError("process handle already closed")
        ms = _winapi.INFINITE if timeout is None else max(0, int(timeout * 1000))
        result = _winapi.WaitForSingleObject(self._handle, ms)
        if result == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(
                cmd="cards-runner-worker", timeout=timeout or 0.0
            )
        self.returncode = _winapi.GetExitCodeProcess(self._handle)
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is not None or self._handle is None:
            return
        try:
            _winapi.TerminateProcess(self._handle, 1)
        except OSError:
            pass

    kill = terminate

    def close(self) -> None:
        if self._handle is not None:
            try:
                _winapi.CloseHandle(self._handle)
            except OSError:
                pass
            self._handle = None

    def __del__(self) -> None:  # pragma: no cover - GC-timing dependent.
        self.close()


def spawn_in_job(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    stdout: int | None = subprocess.DEVNULL,
    stderr: int | None = subprocess.DEVNULL,
) -> ManagedProcess:
    """Spawn `args` as a child process attached to a fresh Job Object.

    On Windows the child is created suspended, assigned to the job,
    then resumed -- no descendant can spawn between process creation
    and job assignment. On POSIX the child starts in its own process
    group instead.

    The Job Object is configured with
    `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` plus
    `JOB_OBJECT_LIMIT_BREAKAWAY_OK` so the daemon can voluntarily
    detach a process if it ever needs to.
    """
    if sys.platform == "win32":
        return _spawn_win32(args, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
    return _spawn_posix(args, cwd=cwd, env=env, stdout=stdout, stderr=stderr)


def _spawn_posix(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    stdout: int | None,
    stderr: int | None,
) -> ManagedProcess:
    popen = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        close_fds=True,
        start_new_session=True,
    )
    return ManagedProcess(popen=popen, _on_posix=True)


def _build_job() -> Any:
    """Create and configure a kill-on-close Job Object. Returns the handle."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
    )
    if not kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        err = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(err, "SetInformationJobObject failed")
    return job


def _dup_inheritable(handle: int) -> int:
    """Duplicate a Win32 handle into an inheritable copy in this process."""
    current = _winapi.GetCurrentProcess()
    return _winapi.DuplicateHandle(
        current, handle, current, 0, True, _winapi.DUPLICATE_SAME_ACCESS
    )


def _spawn_win32(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    stdout: int | None,
    stderr: int | None,
) -> ManagedProcess:
    """Windows path: create suspended, assign to the job, then resume.

    This is the chunk 2b-ii refinement. Creating the worker
    `CREATE_SUSPENDED` and only resuming it after
    `AssignProcessToJobObject` closes the race chunk 1 documented:
    the worker -- and therefore anything the worker spawns -- cannot
    run until the job owns it.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]

    job = _build_job()

    # Build inheritable std handles. stdin is always the NUL device
    # (the worker reads no input); stdout / stderr go to the caller's
    # log files when given, else NUL.
    devnull_fd = os.open(os.devnull, os.O_RDWR)
    opened_handles: list[int] = []

    def _std(value: int | None) -> int:
        if isinstance(value, int) and value >= 0:
            src = msvcrt.get_osfhandle(value)
        else:
            src = msvcrt.get_osfhandle(devnull_fd)
        dup = _dup_inheritable(src)
        opened_handles.append(dup)
        return dup

    hp = ht = None
    try:
        stdin_h = _std(None)
        stdout_h = _std(stdout)
        stderr_h = _std(stderr)

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESTDHANDLES
        startupinfo.hStdInput = stdin_h
        startupinfo.hStdOutput = stdout_h
        startupinfo.hStdError = stderr_h

        flags = (
            _CREATE_SUSPENDED
            | _CREATE_NEW_PROCESS_GROUP
            | _CREATE_UNICODE_ENVIRONMENT
        )
        hp, ht, pid, _tid = _winapi.CreateProcess(
            args[0],                       # application name
            subprocess.list2cmdline(args),  # command line
            None,                          # process security attrs
            None,                          # thread security attrs
            True,                          # inherit handles
            flags,
            env,
            cwd,
            startupinfo,
        )
    except OSError:
        kernel32.CloseHandle(job)
        raise
    finally:
        for dup in opened_handles:
            _winapi.CloseHandle(dup)
        os.close(devnull_fd)

    # The worker is suspended. Assign it to the job before a single
    # instruction of it runs, then resume.
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(hp))):
        err = ctypes.get_last_error()
        try:
            _winapi.TerminateProcess(hp, 1)
        finally:
            _winapi.CloseHandle(ht)
            _winapi.CloseHandle(hp)
            kernel32.CloseHandle(job)
        raise OSError(err, "AssignProcessToJobObject failed")

    if kernel32.ResumeThread(wintypes.HANDLE(int(ht))) == 0xFFFFFFFF:
        err = ctypes.get_last_error()
        try:
            _winapi.TerminateProcess(hp, 1)
        finally:
            _winapi.CloseHandle(ht)
            _winapi.CloseHandle(hp)
            kernel32.CloseHandle(job)
        raise OSError(err, "ResumeThread failed")

    _winapi.CloseHandle(ht)  # thread handle no longer needed.
    process = _Win32Process(hp, pid)
    return ManagedProcess(popen=process, _job_handle=job)
