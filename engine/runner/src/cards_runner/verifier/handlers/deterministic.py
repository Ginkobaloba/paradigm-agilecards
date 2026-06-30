"""Deterministic handlers: file presence, file content, shell exit code.

Pure Python. No network, no LLM, no token spend. Each handler reads
the AC item dict, runs its check against the worktree, and returns a
`HandlerResult(passed, evidence)`. Evidence is what the verifier
writes back into `verifier_notes` when the item fails -- a machine-
readable record of *why*, suitable for the next executor (or human)
to read without re-running the check.

Failure-mode discipline: a handler MUST NOT raise on a check that
just failed (the file does not exist, the command exited non-zero).
Those are normal `passed=False` outcomes. The handler raises only on
its own internal problem -- a malformed AC item, an unreadable file,
a timeout it cannot interpret. The runner converts an internal raise
into a synthetic failed item with the exception's str in evidence;
that matches RUNNER_CONTRACT.md "handlers catch their own exceptions
and convert to fail".
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)


# Hard ceiling on what we'll read into memory for a file_contains
# check. The handler streams the file by line when over this size so a
# multi-gigabyte log file does not OOM the worker. 64 MiB matches the
# /cards skill's library default.
_MAX_INMEMORY_BYTES: int = 64 * 1024 * 1024

# Shell command timeout, in seconds. The contract does not specify a
# canonical default; this is the same conservative cap the daemon's
# `force_kill_after_seconds` uses for the worker process itself, so an
# AC check cannot outlive the worker that ran it.
_DEFAULT_SHELL_TIMEOUT_SEC: float = 60.0


@dataclass(frozen=True)
class HandlerContext:
    """What every handler needs to know about the world.

    `worktree` is the per-card git worktree the executor ran in. All
    relative paths in AC items resolve against it. `env` is the
    scrubbed env block the worker ran with; shell handlers inherit it
    so an AC check sees the same environment the executor saw.
    """

    worktree: Path
    env: dict[str, str] = field(default_factory=dict)
    shell_timeout_sec: float = _DEFAULT_SHELL_TIMEOUT_SEC


@dataclass(frozen=True)
class HandlerResult:
    """One AC item's check result."""

    passed: bool
    evidence: dict[str, Any]


# ---- helpers --------------------------------------------------------


def _resolve_path(ctx: HandlerContext, item: dict[str, Any]) -> Path:
    raw = item.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("AC item is missing a string `path:` field")
    p = Path(raw)
    if not p.is_absolute():
        p = ctx.worktree / p
    # Keep the resolved path visible in evidence even when checks fail
    # for reasons unrelated to the path itself; `Path.resolve` only
    # canonicalizes if the file exists, so use `os.path.abspath` to
    # always produce an absolute string.
    return Path(os.path.abspath(p))


def _read_bounded_text(path: Path) -> tuple[str, bool]:
    """Read the file as UTF-8, returning (text, truncated)."""
    size = path.stat().st_size
    if size <= _MAX_INMEMORY_BYTES:
        return path.read_text(encoding="utf-8", errors="replace"), False
    with path.open("rb") as fh:
        head = fh.read(_MAX_INMEMORY_BYTES)
    return head.decode("utf-8", errors="replace"), True


# ---- handlers -------------------------------------------------------


def handle_file_exists(
    item: dict[str, Any], ctx: HandlerContext
) -> HandlerResult:
    """`type: file_exists` -- the file (or directory) is present."""
    try:
        path = _resolve_path(ctx, item)
    except ValueError as exc:
        return HandlerResult(False, {"error": str(exc)})
    exists = path.exists()
    return HandlerResult(
        passed=exists,
        evidence={
            "path": str(path),
            "exists": exists,
            "is_file": path.is_file() if exists else False,
            "is_dir": path.is_dir() if exists else False,
        },
    )


def handle_file_absent(
    item: dict[str, Any], ctx: HandlerContext
) -> HandlerResult:
    """`type: file_absent` -- the file MUST NOT exist."""
    try:
        path = _resolve_path(ctx, item)
    except ValueError as exc:
        return HandlerResult(False, {"error": str(exc)})
    exists = path.exists()
    return HandlerResult(
        passed=not exists,
        evidence={
            "path": str(path),
            "exists": exists,
        },
    )


def handle_file_contains(
    item: dict[str, Any], ctx: HandlerContext
) -> HandlerResult:
    """`type: file_contains` -- the file matches `pattern:` (substring
    by default; `regex: true` for a Python regex match)."""
    try:
        path = _resolve_path(ctx, item)
    except ValueError as exc:
        return HandlerResult(False, {"error": str(exc)})
    pattern = item.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return HandlerResult(
            False,
            {"path": str(path), "error": "missing string `pattern:` field"},
        )
    if not path.is_file():
        return HandlerResult(
            False,
            {"path": str(path), "error": "file does not exist"},
        )
    try:
        text, truncated = _read_bounded_text(path)
    except OSError as exc:
        return HandlerResult(
            False,
            {"path": str(path), "error": f"could not read file: {exc}"},
        )
    found = _match_pattern(text, pattern, item)
    evidence: dict[str, Any] = {
        "path": str(path),
        "pattern": pattern,
        "regex": bool(item.get("regex")),
        "found": found,
    }
    if truncated:
        evidence["truncated"] = True
        evidence["read_bytes"] = _MAX_INMEMORY_BYTES
    return HandlerResult(passed=found, evidence=evidence)


def handle_file_lacks(
    item: dict[str, Any], ctx: HandlerContext
) -> HandlerResult:
    """`type: file_lacks` -- the file MUST NOT contain `pattern:`.

    A missing file passes (you cannot fail to contain something in a
    file that does not exist). This matches the legacy `grep_absent`
    semantics callers built tests around.
    """
    try:
        path = _resolve_path(ctx, item)
    except ValueError as exc:
        return HandlerResult(False, {"error": str(exc)})
    pattern = item.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return HandlerResult(
            False,
            {"path": str(path), "error": "missing string `pattern:` field"},
        )
    if not path.is_file():
        return HandlerResult(
            True,
            {"path": str(path), "exists": False, "lacks": True},
        )
    try:
        text, truncated = _read_bounded_text(path)
    except OSError as exc:
        return HandlerResult(
            False,
            {"path": str(path), "error": f"could not read file: {exc}"},
        )
    found = _match_pattern(text, pattern, item)
    evidence: dict[str, Any] = {
        "path": str(path),
        "pattern": pattern,
        "regex": bool(item.get("regex")),
        "found": found,
        "lacks": not found,
    }
    if truncated:
        evidence["truncated"] = True
    return HandlerResult(passed=not found, evidence=evidence)


def _match_pattern(text: str, pattern: str, item: dict[str, Any]) -> bool:
    if item.get("regex"):
        import re as _re

        flags = _re.MULTILINE
        if item.get("ignorecase"):
            flags |= _re.IGNORECASE
        try:
            return bool(_re.search(pattern, text, flags))
        except _re.error as exc:
            log.warning("regex %r is invalid: %s", pattern, exc)
            return False
    if item.get("ignorecase"):
        return pattern.lower() in text.lower()
    return pattern in text


def handle_shell(
    item: dict[str, Any], ctx: HandlerContext
) -> HandlerResult:
    """`type: shell` -- the command exits 0 (or matches `expect_exit:`).

    The command runs in the worktree with the scrubbed env block.
    `expect_exit:` overrides the default 0; `expect_contains:` (when
    set) additionally requires the captured stdout/stderr to contain
    the given substring or regex (same `regex:` / `ignorecase:` flags
    as `file_contains`).

    Cross-platform note: on Windows the command is launched through
    `cmd.exe /c <command>` so pipes and built-ins behave; on POSIX it
    goes through `/bin/sh -c`. This matches what a developer running
    the same AC check by hand would expect.
    """
    cmd = item.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return HandlerResult(False, {"error": "missing string `command:` field"})
    expect_exit = int(item.get("expect_exit", 0))

    shell = _resolve_shell()
    args = shell + [cmd]
    timeout = float(item.get("timeout_sec", ctx.shell_timeout_sec))

    try:
        proc = subprocess.run(
            args,
            cwd=str(ctx.worktree),
            env=ctx.env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return HandlerResult(
            False,
            {
                "command": cmd,
                "error": f"command timed out after {timeout:.1f}s",
                "timeout_sec": timeout,
            },
        )
    except OSError as exc:
        return HandlerResult(
            False,
            {"command": cmd, "error": f"could not spawn shell: {exc}"},
        )

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout + "\n" + stderr
    passed = proc.returncode == expect_exit
    expect_contains = item.get("expect_contains")
    if passed and isinstance(expect_contains, str) and expect_contains:
        passed = _match_pattern(combined, expect_contains, item)
    evidence: dict[str, Any] = {
        "command": cmd,
        "exit_code": proc.returncode,
        "expect_exit": expect_exit,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }
    if expect_contains is not None:
        evidence["expect_contains"] = expect_contains
        evidence["expect_contains_matched"] = passed and bool(expect_contains)
    return HandlerResult(passed=passed, evidence=evidence)


def _resolve_shell() -> list[str]:
    """Return the argv prefix that runs a `-c` shell snippet."""
    if sys.platform == "win32":
        comspec = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"
        return [comspec, "/c"]
    sh = shutil.which("sh") or "/bin/sh"
    return [sh, "-c"]


def _tail(text: str, *, lines: int = 40) -> str:
    """Last `lines` lines of `text`, joined with newlines."""
    if not text:
        return ""
    parts = text.splitlines()
    if len(parts) <= lines:
        return "\n".join(parts)
    return "\n".join(parts[-lines:])
