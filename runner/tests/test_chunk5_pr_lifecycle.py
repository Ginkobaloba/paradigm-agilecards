"""Chunk-5 additions to `cards_runner.daemon.pr_lifecycle`.

Covers the new `view_pr`, `pr_diff`, and `pr_review` subprocess wrappers
plus the NullGhRunner default-deny behavior for the same calls.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cards_runner.daemon import pr_lifecycle as pr
from cards_runner.daemon.pr_lifecycle import (
    GhCallResult, NullGhRunner, SubprocessGhRunner,
)


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


def test_view_pr_constructs_args_and_parses_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record: list[dict[str, Any]] = []
    _stub(
        monkeypatch,
        stdout='{"state":"MERGED","mergedAt":"2026-05-20T12:00:00Z"}',
        record=record,
    )
    out = SubprocessGhRunner().view_pr(
        identifier="https://github.com/x/y/pull/7"
    )
    assert out.ok is True
    assert out.parsed["state"] == "MERGED"
    args = record[0]["args"]
    assert args[:4] == ["gh", "pr", "view", "https://github.com/x/y/pull/7"]
    assert "--json" in args
    json_idx = args.index("--json")
    fields = args[json_idx + 1].split(",")
    assert "state" in fields and "mergedAt" in fields


def test_view_pr_failure_propagates_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, returncode=1, stderr="not found")
    out = SubprocessGhRunner().view_pr(identifier="missing")
    assert out.ok is False
    assert "not found" in out.reason


def test_pr_diff_passes_identifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, stdout="--- a\n+++ b\n", record=record)
    out = SubprocessGhRunner().pr_diff(identifier="42", worktree=tmp_path)
    assert out.ok is True
    assert record[0]["args"] == ["gh", "pr", "diff", "42"]
    assert record[0]["cwd"] == str(tmp_path)


def test_pr_review_decision_translation(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, record=record)
    SubprocessGhRunner().pr_review(
        identifier="42", decision="approve", body="lgtm",
    )
    args = record[0]["args"]
    assert args[:4] == ["gh", "pr", "review", "42"]
    assert "--approve" in args
    # body is passed via --body.
    body_idx = args.index("--body")
    assert args[body_idx + 1] == "lgtm"


def test_pr_review_unknown_decision_falls_back_to_comment(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, record=record)
    SubprocessGhRunner().pr_review(
        identifier="42", decision="wat", body="?",
    )
    args = record[0]["args"]
    assert "--comment" in args


def test_pr_review_request_changes_flag(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, record=record)
    SubprocessGhRunner().pr_review(
        identifier="42", decision="request_changes", body="no",
    )
    args = record[0]["args"]
    assert "--request-changes" in args


def test_null_gh_runner_refuses_new_calls() -> None:
    null = NullGhRunner()
    assert null.view_pr(identifier="x").ok is False
    assert null.pr_diff(identifier="x").ok is False
    assert null.pr_review(identifier="x", decision="approve", body="b").ok is False


def test_view_pr_empty_stdout_returns_empty_parsed(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, stdout="")
    out = SubprocessGhRunner().view_pr(identifier="x")
    assert out.ok is True
    assert out.parsed == {}


def test_view_pr_handles_cwd_none(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, stdout='{"state":"OPEN"}', record=record)
    out = SubprocessGhRunner().view_pr(identifier="x", worktree=None)
    assert out.ok is True
    # cwd should have been None (not str(None)).
    assert record[0]["cwd"] is None


def test_pr_diff_no_worktree_keeps_cwd_none(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    record: list[dict[str, Any]] = []
    _stub(monkeypatch, record=record)
    SubprocessGhRunner().pr_diff(identifier="x")
    assert record[0]["cwd"] is None


def test_gh_call_result_default_parsed_dict() -> None:
    r = GhCallResult(ok=True)
    assert r.parsed == {}
