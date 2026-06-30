"""Executor tool belt: file edits, shell, git, sandboxed to the worktree.

RUNNER_CONTRACT.md "Worktree isolation and cross-contamination
defense": each spawned executor works inside its own per-card git
worktree. Chunk 2b-ii landed the metering machinery and stayed
reasoning-only; chunk 3 hangs an actual tool belt off that machinery
so the executor can edit files and run shell commands.

The tools here are deliberately small. They are NOT "general-purpose
Anthropic tools"; they are the minimal vocabulary an autonomous coder
needs to drive one card forward inside one git worktree:

- `file_read`     -- read a UTF-8 file with optional line bounds
- `file_write`    -- write (or create) a UTF-8 file
- `file_replace`  -- replace one occurrence (or all) of a literal string
- `list_dir`      -- list directory contents
- `shell`         -- run a command, capture stdout/stderr/exit code
- `git`           -- safe git verbs only (status, diff, log, add,
                     commit, branch, rev-parse). PUSH IS EXPLICITLY
                     OUT OF SCOPE; merge orchestration is chunk 4.

What this module does NOT do:

- It does not loop the model. The SDK tool-use loop lives in
  `sdk_invoker.py`. This module exports descriptors (input_schema)
  and one synchronous `execute(name, args)` dispatcher.
- It does not modify the store. The worker reads and writes the
  projected card file; the store stays daemon-owned.

Sandboxing posture:
- Every path argument is resolved against the worktree root and
  rejected with `ToolError` if it escapes. This is path-only; it does
  not stop a `shell` invocation from `cd ..` on its own. The Job
  Object wrapping the worker is the hard isolation backstop; this
  layer's job is to make accidental escapes loud, not to be a
  security boundary against a hostile executor.
- The env block handed to `shell` is the worker's already-scrubbed
  block (no `ANTHROPIC_*` except the one explicit key the daemon
  injected; no `GH_TOKEN`; no cloud creds). The tool belt does not
  re-scrub; it inherits.
- `git push` and `git remote ...` are not in the verb allowlist.
  Merge orchestration is chunk 4.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


log = logging.getLogger(__name__)


# Hard cap on the bytes any single read returns. A larger file just
# gets truncated; the executor can paginate or grep instead. This is
# safety against the model asking for a huge file and bursting the
# context budget on the response.
MAX_READ_BYTES: int = 256 * 1024  # 256 KiB

# Per-tool-call wall-clock for `shell` and `git`. The daemon's
# `force_kill_after_seconds` is the absolute backstop; this is a
# friendlier per-call cap.
DEFAULT_TOOL_TIMEOUT_SEC: float = 60.0

# The git subcommands the tool belt is allowed to run. Inspection +
# local commit + branch-create only. No push, no remote, no fetch.
_GIT_ALLOWED_VERBS: frozenset[str] = frozenset({
    "status",
    "diff",
    "log",
    "show",
    "add",
    "rm",
    "mv",
    "reset",          # only without `--hard`; enforced below.
    "restore",
    "commit",
    "branch",         # create / list / delete LOCAL; no -u, no --set-upstream.
    "checkout",
    "switch",
    "rev-parse",
    "ls-files",
    "config",         # safety: read-only `--get` form is fine.
})

# Verbs we MUST refuse outright. They either talk to a remote or are
# destructive in ways that defeat the worktree-isolation model.
_GIT_FORBIDDEN_VERBS: frozenset[str] = frozenset({
    "push",
    "pull",
    "fetch",
    "clone",
    "remote",
    "submodule",
    "worktree",
    "filter-branch",
    "filter-repo",
    "gc",
})


class ToolError(Exception):
    """The tool refused to run (bad argument, escape attempt, etc.).

    Distinct from a tool that ran and failed (e.g. shell exit != 0).
    The latter returns a structured result; only the former raises.
    """


@dataclass(frozen=True)
class ToolResult:
    """One tool dispatch's result.

    `ok` is the high-level "did this do what the model asked": True
    when the tool produced a usable answer, False when the tool ran
    but the operation failed (a missing file on read, a non-zero
    shell exit). `payload` is the JSON-serializable record the
    SdkInvoker forwards to the model as the tool_result content.
    """

    ok: bool
    payload: dict[str, Any]

    def to_text(self) -> str:
        """Render the payload as a JSON string for the SDK tool_result."""
        return json.dumps(self.payload, sort_keys=True, ensure_ascii=False)


# The Anthropic-SDK tool descriptor dicts. Kept in this module so the
# SdkInvoker is just `tools=ToolBelt.descriptors()` without having to
# track schema drift in two places.
TOOL_DESCRIPTORS: tuple[dict[str, Any], ...] = (
    {
        "name": "file_read",
        "description": (
            "Read a UTF-8 text file under the worktree. Returns the "
            "file contents (truncated at 256 KiB) with line offsets. "
            "Use `start_line`/`end_line` (1-indexed, inclusive) to "
            "read a slice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path relative to the worktree root. Absolute "
                        "paths that escape the worktree are rejected."
                    ),
                },
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
    },
    {
        "name": "file_write",
        "description": (
            "Write a UTF-8 text file. Creates parent directories as "
            "needed. Overwrites any existing file at `path`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "file_replace",
        "description": (
            "Replace one occurrence of `old` with `new` in a UTF-8 "
            "file. Fails if `old` does not appear, or appears more "
            "than once when `replace_all` is false. Use this for "
            "surgical edits; use file_write to overwrite a whole file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "list_dir",
        "description": (
            "List directory contents. Returns a list of "
            "{name, kind} entries. `kind` is one of file, dir, link."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "shell",
        "description": (
            "Run a shell command in the worktree. Returns "
            "{exit_code, stdout, stderr, timed_out}. Output is "
            "truncated to the last ~16 KiB per stream. Inherits the "
            "scrubbed env block the worker was started with."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_sec": {
                    "type": "number",
                    "default": DEFAULT_TOOL_TIMEOUT_SEC,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "git",
        "description": (
            "Run a git subcommand on the worktree. Only inspection "
            "and local-commit verbs are allowed; push, pull, fetch, "
            "clone, remote, and submodule are refused. Returns "
            "{exit_code, stdout, stderr}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "timeout_sec": {
                    "type": "number",
                    "default": DEFAULT_TOOL_TIMEOUT_SEC,
                },
            },
            "required": ["args"],
        },
    },
    {
        "name": "report_done",
        "description": (
            "Signal that the work is complete. End your turn with "
            "this when no more tool calls are needed. `confidence` is "
            "your self-reported 0.0-1.0 score the cascade reads."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["summary", "confidence"],
        },
    },
)


@dataclass
class ToolBelt:
    """Sandboxed file/shell/git tools rooted at one worktree.

    The belt is stateless across cards; the daemon spawns a fresh
    worker per claim, the worker constructs one belt, and the
    SdkInvoker dispatches to it. Tests construct one directly against
    a tmp_path.

    `read_only` is a future hook for tier-0 "verifier mode" use; for
    now it just blocks the write/edit/shell/git verbs and lets the
    read verbs through.
    """

    worktree: Path
    env: dict[str, str]
    shell_timeout_sec: float = DEFAULT_TOOL_TIMEOUT_SEC
    read_only: bool = False

    @classmethod
    def descriptors(cls) -> tuple[dict[str, Any], ...]:
        return TOOL_DESCRIPTORS

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Dispatch one named tool call. Raises `ToolError` on refuse."""
        handler = _DISPATCH.get(name)
        if handler is None:
            raise ToolError(f"unknown tool {name!r}")
        if self.read_only and name in _MUTATING_TOOLS:
            raise ToolError(f"tool {name!r} not allowed in read-only mode")
        return handler(self, args)

    # ---- per-tool implementations ---------------------------------

    def _resolve(self, raw: Any, *, allow_root: bool = False) -> Path:
        if raw is None and allow_root:
            return self.worktree
        if not isinstance(raw, str) or not raw:
            raise ToolError("argument `path` must be a non-empty string")
        p = Path(raw)
        if p.is_absolute():
            candidate = Path(os.path.abspath(p))
        else:
            candidate = Path(os.path.abspath(self.worktree / p))
        # Refuse paths that escape the worktree.
        wt = Path(os.path.abspath(self.worktree))
        try:
            candidate.resolve(strict=False).relative_to(wt.resolve(strict=False))
        except ValueError as exc:
            raise ToolError(
                f"path {raw!r} escapes the worktree at {wt}"
            ) from exc
        return candidate

    def _file_read(self, args: dict[str, Any]) -> ToolResult:
        path = self._resolve(args.get("path"))
        if not path.is_file():
            return ToolResult(False, {"path": str(path), "error": "not a file"})
        try:
            with path.open("rb") as fh:
                blob = fh.read(MAX_READ_BYTES + 1)
        except OSError as exc:
            return ToolResult(False, {"path": str(path), "error": str(exc)})
        truncated = len(blob) > MAX_READ_BYTES
        text = blob[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = args.get("start_line")
        end = args.get("end_line")
        if isinstance(start, int) or isinstance(end, int):
            s = max(1, int(start)) if isinstance(start, int) else 1
            e = int(end) if isinstance(end, int) else len(lines)
            e = max(s, e)
            sliced = lines[s - 1: e]
            return ToolResult(
                True,
                {
                    "path": str(path),
                    "start_line": s,
                    "end_line": min(e, s - 1 + len(sliced)),
                    "total_lines": len(lines),
                    "truncated": truncated,
                    "content": "\n".join(sliced),
                },
            )
        return ToolResult(
            True,
            {
                "path": str(path),
                "total_lines": len(lines),
                "truncated": truncated,
                "content": text,
            },
        )

    def _file_write(self, args: dict[str, Any]) -> ToolResult:
        path = self._resolve(args.get("path"))
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolError("argument `content` must be a string")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(content, encoding="utf-8", newline="")
        except OSError as exc:
            return ToolResult(False, {"path": str(path), "error": str(exc)})
        return ToolResult(
            True,
            {"path": str(path), "bytes_written": len(content.encode("utf-8"))},
        )

    def _file_replace(self, args: dict[str, Any]) -> ToolResult:
        path = self._resolve(args.get("path"))
        old = args.get("old")
        new = args.get("new")
        if not isinstance(old, str) or old == "":
            raise ToolError("argument `old` must be a non-empty string")
        if not isinstance(new, str):
            raise ToolError("argument `new` must be a string")
        if not path.is_file():
            return ToolResult(False, {"path": str(path), "error": "not a file"})
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(False, {"path": str(path), "error": str(exc)})
        replace_all = bool(args.get("replace_all"))
        occurrences = text.count(old)
        if occurrences == 0:
            return ToolResult(
                False,
                {"path": str(path), "error": "old string not found in file"},
            )
        if occurrences > 1 and not replace_all:
            return ToolResult(
                False,
                {
                    "path": str(path),
                    "error": "old string appears multiple times; set "
                             "replace_all=true to replace every occurrence",
                    "occurrences": occurrences,
                },
            )
        new_text = (
            text.replace(old, new) if replace_all else text.replace(old, new, 1)
        )
        try:
            path.write_text(new_text, encoding="utf-8", newline="")
        except OSError as exc:
            return ToolResult(False, {"path": str(path), "error": str(exc)})
        return ToolResult(
            True,
            {
                "path": str(path),
                "occurrences_replaced": occurrences if replace_all else 1,
            },
        )

    def _list_dir(self, args: dict[str, Any]) -> ToolResult:
        path = self._resolve(args.get("path"), allow_root=True)
        if not path.is_dir():
            return ToolResult(False, {"path": str(path), "error": "not a directory"})
        try:
            entries = []
            for child in sorted(path.iterdir()):
                kind = (
                    "link" if child.is_symlink()
                    else "dir" if child.is_dir()
                    else "file" if child.is_file()
                    else "other"
                )
                entries.append({"name": child.name, "kind": kind})
        except OSError as exc:
            return ToolResult(False, {"path": str(path), "error": str(exc)})
        return ToolResult(True, {"path": str(path), "entries": entries})

    def _shell(self, args: dict[str, Any]) -> ToolResult:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolError("argument `command` must be a non-empty string")
        timeout = float(args.get("timeout_sec", self.shell_timeout_sec))
        shell_argv = _resolve_shell() + [command]
        return _run_subprocess(shell_argv, self.worktree, self.env, timeout, label="shell", echo_command=command)

    def _git(self, args: dict[str, Any]) -> ToolResult:
        raw_args = args.get("args")
        if not isinstance(raw_args, list) or not raw_args:
            raise ToolError("argument `args` must be a non-empty list of strings")
        argv = [str(a) for a in raw_args]
        verb = argv[0].lstrip("-")
        if verb in _GIT_FORBIDDEN_VERBS:
            raise ToolError(
                f"git verb {verb!r} is forbidden by the tool belt; "
                "merge orchestration is the daemon's job (chunk 4)"
            )
        if verb not in _GIT_ALLOWED_VERBS:
            raise ToolError(
                f"git verb {verb!r} is not on the allowlist "
                f"({sorted(_GIT_ALLOWED_VERBS)})"
            )
        if verb == "reset" and any(a == "--hard" for a in argv[1:]):
            raise ToolError(
                "git reset --hard is refused; use `git restore` "
                "for tracked-file resets"
            )
        if verb == "branch" and any(
            a == "-u"
            or a == "--set-upstream"
            or a.startswith("--set-upstream-to")
            or a.startswith("--set-upstream=")
            for a in argv[1:]
        ):
            raise ToolError(
                "git branch --set-upstream is refused; the tool belt "
                "does not talk to remotes"
            )
        timeout = float(args.get("timeout_sec", self.shell_timeout_sec))
        git_argv = ["git", *argv]
        return _run_subprocess(git_argv, self.worktree, self.env, timeout, label="git", echo_command=" ".join(git_argv))

    def _report_done(self, args: dict[str, Any]) -> ToolResult:
        summary = args.get("summary")
        confidence = args.get("confidence")
        if not isinstance(summary, str):
            raise ToolError("argument `summary` must be a string")
        try:
            conf = float(confidence)
        except (TypeError, ValueError) as exc:
            raise ToolError("argument `confidence` must be a number") from exc
        conf = max(0.0, min(1.0, conf))
        return ToolResult(
            True,
            {"summary": summary, "confidence": conf, "terminal": True},
        )


# ---- module-level helpers -------------------------------------------


def _resolve_shell() -> list[str]:
    if sys.platform == "win32":
        comspec = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"
        return [comspec, "/c"]
    sh = shutil.which("sh") or "/bin/sh"
    return [sh, "-c"]


def _run_subprocess(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    *,
    label: str,
    echo_command: str,
) -> ToolResult:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ToolResult(
            False,
            {
                "label": label,
                "command": echo_command,
                "timed_out": True,
                "timeout_sec": timeout,
                "stdout": _tail(exc.stdout if isinstance(exc.stdout, str) else ""),
                "stderr": _tail(exc.stderr if isinstance(exc.stderr, str) else ""),
            },
        )
    except OSError as exc:
        return ToolResult(
            False,
            {
                "label": label,
                "command": echo_command,
                "error": f"could not spawn {label}: {exc}",
            },
        )
    return ToolResult(
        proc.returncode == 0,
        {
            "label": label,
            "command": echo_command,
            "exit_code": proc.returncode,
            "stdout": _tail(proc.stdout or ""),
            "stderr": _tail(proc.stderr or ""),
        },
    )


def _tail(text: str, *, max_bytes: int = 16 * 1024) -> str:
    if not text:
        return ""
    if len(text) <= max_bytes:
        return text
    return "...[truncated]...\n" + text[-max_bytes:]


# Mapping from tool name to bound method on the belt instance.
_DISPATCH: dict[str, Callable[[ToolBelt, dict[str, Any]], ToolResult]] = {
    "file_read": ToolBelt._file_read,
    "file_write": ToolBelt._file_write,
    "file_replace": ToolBelt._file_replace,
    "list_dir": ToolBelt._list_dir,
    "shell": ToolBelt._shell,
    "git": ToolBelt._git,
    "report_done": ToolBelt._report_done,
}


# Verbs that change state; read-only mode refuses these.
_MUTATING_TOOLS: frozenset[str] = frozenset({
    "file_write", "file_replace", "shell", "git",
})
