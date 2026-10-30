"""rescore.py: each check must be able to fire (a tampered row, an altered
reply, a missing reply), and a faithful row must come back identical."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import rescore
import run_bakeoff as rb

CARD = rb.Card("bk-01", "body")


def _score(card: rb.Card, text: str) -> dict[str, Any]:
    ok = "GOOD" in text
    return {"parsed": True, "parse_failure": None, "blocks": 1, "touched_tests": [],
            "suspect": [], "passed": ok, "ac": [], "verifier_timeout": False,
            "forged": False, "think_in_block": False}


def _results(tmp_path: Path, replies: dict[int, str], **row_over: Any) -> Path:
    out = tmp_path / "A-1"
    (out / "replies").mkdir(parents=True)
    rows = []
    for run, text in replies.items():
        # As the harness does: write_text, so the saved file has CRLF on Windows.
        (out / "replies" / f"bk-01-r{run}.txt").write_text(text, encoding="utf-8")
        row = {"card_id": "bk-01", "run": run, "error_kind": None, "done_reason": "stop",
               "prompt_tokens": 10, "output_tokens": 10,
               "reply_sha256": hashlib.sha256(text.encode()).hexdigest()}
        row.update(rescore.rescore_row(row, CARD, text, _score))
        row.update(row_over)
        rows.append(row)
    (out / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return out


def test_a_faithful_row_is_identical(tmp_path: Path) -> None:
    out = _results(tmp_path, {1: "GOOD\nline two\n", 2: "bad\n"})
    report = rescore.rescore_dir(out, score=_score, cards=[CARD])
    assert report.ok and report.identical == report.rows == 2


def test_a_tampered_recorded_row_is_a_difference(tmp_path: Path) -> None:
    out = _results(tmp_path, {1: "GOOD\n"}, passed=False, outcome="ac_failure")
    report = rescore.rescore_dir(out, score=_score, cards=[CARD])
    assert not report.ok and len(report.differences) == 1
    assert report.differences[0]["diff"]["passed"] == {"recorded": False, "now": True}


def test_an_altered_reply_is_not_rescorable_not_a_difference(tmp_path: Path) -> None:
    out = _results(tmp_path, {1: "GOOD\n"})
    (out / "replies" / "bk-01-r1.txt").write_text("GOOD but edited\n", encoding="utf-8")
    report = rescore.rescore_dir(out, score=_score, cards=[CARD])
    assert not report.ok and report.differences == []
    assert report.unscorable == ["A-1/bk-01-r1: saved reply does not match reply_sha256"]


def test_a_missing_reply_is_not_rescorable(tmp_path: Path) -> None:
    out = _results(tmp_path, {1: "GOOD\n"})
    (out / "replies" / "bk-01-r1.txt").unlink()
    report = rescore.rescore_dir(out, score=_score, cards=[CARD])
    assert not report.ok and report.unscorable == ["A-1/bk-01-r1: reply file missing"]


def test_infra_rows_are_skipped_and_counted(tmp_path: Path) -> None:
    out = _results(tmp_path, {1: "GOOD\n"}, error_kind="infra")
    report = rescore.rescore_dir(out, score=_score, cards=[CARD])
    assert report.rows == 0 and report.skipped_infra == 1
