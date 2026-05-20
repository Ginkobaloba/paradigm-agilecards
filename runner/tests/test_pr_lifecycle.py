"""Tests for `cards_runner.daemon.pr_lifecycle.SubprocessGhRunner`.

The real subprocess + the real gh CLI are NOT exercised here -- the
runner CI does not have GitHub credentials and must never accidentally
make a network call. `monkeypatch` swaps `subprocess.run` for a fake
that records the args and returns scripted CompletedProcess values; the
tests assert (a) the right args are constructed and (b) failures map
to the right `GhCallResult` shape.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from cards_runner.daemon import pr_lifecycle as pr
from cards_runner.daemon.pr_lifecycle import GhCallResult, SubprocessGhRunner


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    record: list[dict[str, Any]] | None = None,
) -> None:
    def _fake_run(args: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        if record is not None:
            record.append({"args": args, **kwargs})
        return _FakeCompletedProcess(returncode, stdout, stderr)

    monkeypatch.setattr(pr.subprocess, "run", _fake_run)


def test_is_available_uses_shutil_which(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both binaries present.
    monkeypatch.setattr(pr.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert SubprocessGhRunner().is_available() is True

    # gh missing.
    monkeypatch.setattr(pr.shutil, "which", lambda name: None if name == "gh" else "/usr/bin/git")
    assert SubprocessGhRunner().is_available() is False


def test_push_constructs_git_push_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, returncode=0, stdout="ok", record=record)
    runner = SubprocessGhRunner(git_path="git")
    out = runner.push(tmp_path, "card/abc")
    assert out.ok is True
    assert record[0]["args"] == ["git", "push", "-u", "origin", "card/abc"]
    assert record[0]["cwd"] == str(tmp_path)


def test_push_set_upstream_false_omits_dash_u(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, record=record)
    SubprocessGhRunner().push(tmp_path, "feature/x", set_upstream=False)
    assert record[0]["args"] == ["git", "push", "origin", "feature/x"]


def test_open_pr_returns_url_in_parsed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, stdout="https://github.com/x/y/pull/77\n")
    runner = SubprocessGhRunner()
    out = runner.open_pr(tmp_path, title="t", body="b", base="main")
    assert out.ok is True
    assert out.parsed["pr_url"] == "https://github.com/x/y/pull/77"


def test_open_pr_draft_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, stdout="https://x/y/pull/1\n", record=record)
    SubprocessGhRunner().open_pr(tmp_path, title="t", body="b", base="main", draft=True)
    assert "--draft" in record[0]["args"]


def test_open_pr_failure_propagates_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, returncode=1, stderr="auth required")
    out = SubprocessGhRunner().open_pr(tmp_path, title="t", body="b", base="main")
    assert out.ok is False
    assert "auth required" in out.reason


def test_merge_pr_uses_auto_and_delete_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, record=record)
    SubprocessGhRunner().merge_pr(tmp_path, identifier="77", strategy="squash")
    args = record[0]["args"]
    assert args == ["gh", "pr", "merge", "77", "--squash", "--auto", "--delete-branch"]


def test_merge_pr_strategy_translation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, record=record)
    SubprocessGhRunner().merge_pr(tmp_path, identifier="77", strategy="rebase")
    assert "--rebase" in record[0]["args"]
    record.clear()
    SubprocessGhRunner().merge_pr(tmp_path, identifier="77", strategy="merge")
    assert "--merge" in record[0]["args"]
    record.clear()
    # Unknown strategy falls back to squash (safer default).
    SubprocessGhRunner().merge_pr(tmp_path, identifier="77", strategy="rocket")
    assert "--squash" in record[0]["args"]


def test_file_not_found_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(args: list[str], **kwargs: Any) -> None:
        raise FileNotFoundError(2, "No such file", args[0])

    monkeypatch.setattr(pr.subprocess, "run", _raise)
    out = SubprocessGhRunner(gh_path="not-gh").open_pr(
        tmp_path, title="t", body="b", base="main"
    )
    assert out.ok is False
    assert "binary not found" in out.reason


def test_timeout_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(args: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(args, timeout=1.0)

    monkeypatch.setattr(pr.subprocess, "run", _raise)
    out = SubprocessGhRunner(timeout_sec=1.0).push(tmp_path, "feature/y")
    assert out.ok is False
    assert "timed out" in out.reason


def test_parse_pr_view_json_helper() -> None:
    assert pr.parse_pr_view_json("{}") == {}
    parsed = pr.parse_pr_view_json('{"state": "merged", "mergedAt": "2026-05-20T00:00:00Z"}')
    assert parsed["state"] == "merged"
    # Non-dict / unparseable input returns {}.
    assert pr.parse_pr_view_json("nope") == {}
    assert pr.parse_pr_view_json("[1, 2]") == {}


def test_open_pr_empty_stdout_returns_empty_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, stdout="")
    out = SubprocessGhRunner().open_pr(tmp_path, title="t", body="b", base="main")
    assert out.ok is True
    assert out.parsed.get("pr_url") == ""


def test_call_result_carries_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, returncode=3, stderr="boom")
    out = SubprocessGhRunner().push(tmp_path, "x")
    assert out.ok is False
    assert out.exit_code == 3
    assert "boom" in out.reason


def test_gh_call_result_default_ok_field() -> None:
    out = GhCallResult(ok=True)
    assert out.ok is True
    assert out.parsed == {}
    assert out.stdout == ""
