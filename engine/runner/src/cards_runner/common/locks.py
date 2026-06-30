"""File-based locks.

Two kinds:

1. **Daemon singleton lock** (`.daemon.lock` at TODO root). Holds the
   daemon's PID. A second daemon process refuses to start when the
   lock file is held by a live PID.

2. **Worktree-creation mutex** (`.runner.lock` at TODO root). Held only
   for the duration of a single `git worktree add` call. Serializes
   concurrent worktree creation across daemons or across cards to
   defeat the `.git/config.lock` race (Claude Code issue #34645).

Both wrap the same `FileLock` primitive: a lockfile that uses
`msvcrt.locking` on Windows and `fcntl.flock` on POSIX. The lock is
released when the file handle is closed, including process death.
"""
from __future__ import annotations

import errno
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator


class LockHeldError(Exception):
    """Raised when an attempt to acquire a lock fails."""


def _lock_handle(handle: IO[bytes], *, blocking: bool) -> bool:
    """Lock a file handle. Returns True on success.

    POSIX uses `fcntl.flock`. Windows uses `msvcrt.locking`. Both are
    advisory and process-scoped; both release on handle close.
    """
    if sys.platform == "win32":
        import msvcrt  # type: ignore[import-not-found]
        mode = (
            msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        )
        try:
            msvcrt.locking(handle.fileno(), mode, 1)
            return True
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EDEADLK):
                return False
            raise
    else:
        import fcntl
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
            return True
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                return False
            raise


def _ensure_byte(handle: IO[bytes]) -> None:
    """Make sure there is at least one byte in the file.

    Some platforms refuse to lock a zero-length region. We write a
    single byte (the placeholder PID rewrite happens later for the
    daemon lock; here we just make sure the byte exists).
    """
    try:
        size = os.fstat(handle.fileno()).st_size
    except OSError:
        size = 0
    if size == 0:
        handle.write(b"\x00")
        handle.flush()
        handle.seek(0)


class FileLock:
    """A file-backed lock.

    `path` is created on `acquire` if missing. Use as a context
    manager or call `acquire` / `release` explicitly.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    def acquire(self, blocking: bool = True, timeout_sec: float | None = None) -> None:
        """Acquire the lock. Raises `LockHeldError` on contention.

        If `timeout_sec` is set we poll with a short sleep until we
        get it or the deadline expires. This is the path the daemon
        uses when waiting for the worktree mutex; we never want to
        block indefinitely inside the poll loop.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = None if timeout_sec is None else time.monotonic() + timeout_sec
        handle = open(self.path, "r+b" if self.path.exists() else "w+b")
        try:
            _ensure_byte(handle)
            while True:
                if _lock_handle(handle, blocking=False):
                    self._handle = handle
                    return
                if not blocking:
                    raise LockHeldError(f"{self.path} is locked")
                if deadline is not None and time.monotonic() >= deadline:
                    raise LockHeldError(
                        f"timed out waiting for {self.path}"
                    )
                time.sleep(0.05)
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        finally:
            self._handle = None

    def write_pid(self, pid: int) -> None:
        """Write the holder PID into the lockfile.

        Called by the daemon after `acquire`. The next daemon that
        starts reads this to decide whether the prior holder is still
        alive.
        """
        if self._handle is None:
            raise RuntimeError("write_pid called without an active lock")
        self._handle.seek(0)
        self._handle.truncate(0)
        self._handle.write(str(pid).encode("ascii") + b"\n")
        self._handle.flush()

    def read_pid(self) -> int | None:
        """Read the PID stored in the lockfile, or None if unreadable.

        Static method-ish: works even when we do not hold the lock.
        """
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return None
        text = data.decode("ascii", errors="replace").strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()


def pid_alive(pid: int) -> bool:
    """Best-effort liveness check.

    On Windows, signal 0 is not available; we call `OpenProcess` via
    ctypes. On POSIX, `os.kill(pid, 0)` is the canonical probe.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        # Confirm it has not already exited.
        exit_code = ctypes.c_ulong(0)
        STILL_ACTIVE = 259
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return bool(ok) and exit_code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The PID exists but is owned by another user. Treat as alive.
        return True


@contextmanager
def held_worktree_mutex(
    runner_lock_path: Path,
    timeout_sec: float = 60.0,
) -> Iterator[None]:
    """Hold the global `.runner.lock` for the duration of the `with` block.

    Use sparingly: the lock is held only across the actual
    `git worktree add` call. Anything else inside the block adds
    contention for sibling claim threads.
    """
    lock = FileLock(runner_lock_path)
    lock.acquire(blocking=True, timeout_sec=timeout_sec)
    try:
        yield
    finally:
        lock.release()
