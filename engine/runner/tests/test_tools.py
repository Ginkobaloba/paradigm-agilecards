"""Executor tool belt -- file/shell/git tools sandboxed to a worktree."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cards_runner.worker_stub.tools import (
    TOOL_DESCRIPTORS,
    ToolBelt,
    ToolError,
)


@pytest.fixture
def belt(tmp_path: Path) -> ToolBelt:
    return ToolBelt(worktree=tmp_path, env=dict(os.environ))


def test_descriptors_cover_canonical_tool_set() -> None:
    names = {d["name"] for d in TOOL_DESCRIPTORS}
    assert names == {
        "file_read", "file_write", "file_replace", "list_dir",
        "shell", "git", "report_done",
    }


def test_file_write_then_read_roundtrip(belt: ToolBelt) -> None:
    result = belt.execute("file_write", {"path": "hello.txt", "content": "hi!"})
    assert result.ok is True
    assert result.payload["bytes_written"] == 3
    rd = belt.execute("file_read", {"path": "hello.txt"})
    assert rd.ok is True
    assert rd.payload["content"] == "hi!"


def test_file_read_supports_line_slices(belt: ToolBelt) -> None:
    belt.execute("file_write", {"path": "a.txt", "content": "1\n2\n3\n4\n5"})
    rd = belt.execute("file_read", {"path": "a.txt", "start_line": 2, "end_line": 4})
    assert rd.ok is True
    assert rd.payload["content"] == "2\n3\n4"
    assert rd.payload["total_lines"] == 5


def test_file_read_missing_returns_error_not_raise(belt: ToolBelt) -> None:
    rd = belt.execute("file_read", {"path": "nope.txt"})
    assert rd.ok is False
    assert "not a file" in rd.payload["error"]


def test_file_replace_single_occurrence(belt: ToolBelt) -> None:
    belt.execute("file_write", {"path": "code.py", "content": "x = 1\n"})
    rep = belt.execute(
        "file_replace",
        {"path": "code.py", "old": "x = 1", "new": "x = 42"},
    )
    assert rep.ok is True
    assert rep.payload["occurrences_replaced"] == 1
    rd = belt.execute("file_read", {"path": "code.py"})
    assert "x = 42" in rd.payload["content"]


def test_file_replace_rejects_multiple_without_flag(belt: ToolBelt) -> None:
    belt.execute("file_write", {"path": "code.py", "content": "x = 1\nx = 1\n"})
    rep = belt.execute(
        "file_replace",
        {"path": "code.py", "old": "x = 1", "new": "x = 2"},
    )
    assert rep.ok is False
    assert "multiple" in rep.payload["error"]
    assert rep.payload["occurrences"] == 2


def test_file_replace_all_replaces_every(belt: ToolBelt) -> None:
    belt.execute("file_write", {"path": "code.py", "content": "x = 1\nx = 1\n"})
    rep = belt.execute(
        "file_replace",
        {"path": "code.py", "old": "x = 1", "new": "x = 2", "replace_all": True},
    )
    assert rep.ok is True
    assert rep.payload["occurrences_replaced"] == 2


def test_path_escape_is_refused(belt: ToolBelt) -> None:
    with pytest.raises(ToolError, match="escapes the worktree"):
        belt.execute("file_read", {"path": "../etc/passwd"})


def test_list_dir_returns_entries(belt: ToolBelt) -> None:
    belt.execute("file_write", {"path": "a.txt", "content": "x"})
    belt.execute("file_write", {"path": "sub/b.txt", "content": "y"})
    ls = belt.execute("list_dir", {"path": "."})
    assert ls.ok is True
    names = {e["name"] for e in ls.payload["entries"]}
    assert "a.txt" in names and "sub" in names


def test_shell_captures_stdout_and_exit_code(belt: ToolBelt) -> None:
    if sys.platform == "win32":
        cmd = "echo hello"
    else:
        cmd = "echo hello"
    result = belt.execute("shell", {"command": cmd})
    assert result.ok is True
    assert "hello" in result.payload["stdout"]
    assert result.payload["exit_code"] == 0


def test_shell_failure_is_not_a_refuse(belt: ToolBelt) -> None:
    # A command that exits non-zero is `ok=False` but does NOT raise.
    if sys.platform == "win32":
        cmd = "exit 7"
    else:
        cmd = "exit 7"
    result = belt.execute("shell", {"command": cmd})
    assert result.ok is False
    assert result.payload["exit_code"] == 7


def test_git_refuses_push(belt: ToolBelt) -> None:
    with pytest.raises(ToolError, match="forbidden"):
        belt.execute("git", {"args": ["push", "origin", "main"]})


def test_git_refuses_unknown_verb(belt: ToolBelt) -> None:
    with pytest.raises(ToolError, match="not on the allowlist"):
        belt.execute("git", {"args": ["pizza"]})


def test_git_refuses_reset_hard(belt: ToolBelt) -> None:
    with pytest.raises(ToolError, match="--hard is refused"):
        belt.execute("git", {"args": ["reset", "--hard", "HEAD"]})


def test_git_refuses_branch_set_upstream(belt: ToolBelt) -> None:
    with pytest.raises(ToolError, match="--set-upstream"):
        belt.execute("git", {"args": ["branch", "--set-upstream-to=origin/main"]})


def test_unknown_tool_is_refused(belt: ToolBelt) -> None:
    with pytest.raises(ToolError, match="unknown tool"):
        belt.execute("teleport", {})


def test_report_done_returns_terminal_payload(belt: ToolBelt) -> None:
    r = belt.execute("report_done", {"summary": "done", "confidence": 0.92})
    assert r.ok is True
    assert r.payload["terminal"] is True
    assert r.payload["confidence"] == pytest.approx(0.92)


def test_read_only_mode_blocks_writes(tmp_path: Path) -> None:
    ro = ToolBelt(worktree=tmp_path, env={}, read_only=True)
    with pytest.raises(ToolError, match="read-only"):
        ro.execute("file_write", {"path": "x", "content": "y"})
    # but reading still works (the file doesn't exist; ok=False, no raise).
    rd = ro.execute("file_read", {"path": "x"})
    assert rd.ok is False
