"""Deterministic verifier handlers: file_exists / file_absent /
file_contains / file_lacks / shell.

Pure-Python checks, token-free. The handlers are dispatched by the
verifier runner; here we exercise each one directly against a tmp
worktree to keep the test surface small.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cards_runner.verifier.handlers.deterministic import (
    HandlerContext,
    handle_file_absent,
    handle_file_contains,
    handle_file_exists,
    handle_file_lacks,
    handle_shell,
)


@pytest.fixture
def ctx(tmp_path: Path) -> HandlerContext:
    return HandlerContext(worktree=tmp_path, env={})


def test_file_exists_passes_when_present(ctx: HandlerContext) -> None:
    (ctx.worktree / "README.md").write_text("hi", encoding="utf-8")
    r = handle_file_exists({"path": "README.md"}, ctx)
    assert r.passed is True
    assert r.evidence["exists"] is True
    assert r.evidence["is_file"] is True


def test_file_exists_fails_when_missing(ctx: HandlerContext) -> None:
    r = handle_file_exists({"path": "README.md"}, ctx)
    assert r.passed is False
    assert r.evidence["exists"] is False


def test_file_absent_inverts_existence(ctx: HandlerContext) -> None:
    (ctx.worktree / "x").write_text("y", encoding="utf-8")
    assert handle_file_absent({"path": "x"}, ctx).passed is False
    assert handle_file_absent({"path": "missing"}, ctx).passed is True


def test_file_contains_substring(ctx: HandlerContext) -> None:
    (ctx.worktree / "log.txt").write_text("line one\nfoo bar\nlast", encoding="utf-8")
    assert handle_file_contains(
        {"path": "log.txt", "pattern": "foo bar"}, ctx
    ).passed is True
    assert handle_file_contains(
        {"path": "log.txt", "pattern": "zzz"}, ctx
    ).passed is False


def test_file_contains_regex(ctx: HandlerContext) -> None:
    (ctx.worktree / "log.txt").write_text("ERROR: something\n", encoding="utf-8")
    r = handle_file_contains(
        {"path": "log.txt", "pattern": r"^ERROR: \w+$", "regex": True}, ctx
    )
    assert r.passed is True
    assert r.evidence["regex"] is True


def test_file_contains_missing_file_fails_explicitly(ctx: HandlerContext) -> None:
    r = handle_file_contains({"path": "ghost", "pattern": "x"}, ctx)
    assert r.passed is False
    assert "does not exist" in r.evidence["error"]


def test_file_lacks_passes_when_pattern_absent(ctx: HandlerContext) -> None:
    (ctx.worktree / "code.py").write_text("def f():\n    pass\n", encoding="utf-8")
    r = handle_file_lacks({"path": "code.py", "pattern": "TODO"}, ctx)
    assert r.passed is True
    assert r.evidence["lacks"] is True


def test_file_lacks_passes_when_file_missing(ctx: HandlerContext) -> None:
    # `grep_absent` semantics: missing file passes -- you cannot fail
    # to contain something in a file that does not exist.
    r = handle_file_lacks({"path": "missing", "pattern": "x"}, ctx)
    assert r.passed is True


def test_shell_exit_zero_passes(ctx: HandlerContext) -> None:
    r = handle_shell({"command": "echo ok"}, ctx)
    assert r.passed is True
    assert r.evidence["exit_code"] == 0
    assert "ok" in r.evidence["stdout_tail"]


def test_shell_nonzero_fails(ctx: HandlerContext) -> None:
    cmd = "exit 5"
    r = handle_shell({"command": cmd}, ctx)
    assert r.passed is False
    assert r.evidence["exit_code"] == 5


def test_shell_expect_exit_override(ctx: HandlerContext) -> None:
    cmd = "exit 3"
    r = handle_shell({"command": cmd, "expect_exit": 3}, ctx)
    assert r.passed is True


def test_shell_expect_contains(ctx: HandlerContext) -> None:
    r = handle_shell(
        {"command": "echo hello world", "expect_contains": "world"}, ctx
    )
    assert r.passed is True


def test_shell_timeout(ctx: HandlerContext) -> None:
    # Tiny timeout against a sleep; we don't need to be precise about
    # what platform-portable sleep syntax looks like -- on both
    # Windows cmd.exe and POSIX sh, the command below sleeps long
    # enough to trigger the 0.1s timeout.
    if sys.platform == "win32":
        cmd = "ping -n 5 127.0.0.1 > nul"
    else:
        cmd = "sleep 2"
    r = handle_shell({"command": cmd, "timeout_sec": 0.2}, ctx)
    assert r.passed is False
    # Either timed out or just plain failed -- both acceptable, but
    # the evidence must be a structured dict not an unhandled crash.
    assert "command" in r.evidence


def test_shell_missing_command_fails(ctx: HandlerContext) -> None:
    r = handle_shell({}, ctx)
    assert r.passed is False
    assert "missing" in r.evidence["error"]
