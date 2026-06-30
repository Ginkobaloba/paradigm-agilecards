"""Tests for `cards_runner.daemon.reviewer_history`."""
from __future__ import annotations

from pathlib import Path

from cards_runner.common.types import RuntimePaths
from cards_runner.daemon.reviewer_history import (
    HISTORY_FILENAME,
    HistoryEntry,
    aggregate,
    append_entry,
    history_path,
    read_history,
)


def _paths(tmp_path: Path) -> RuntimePaths:
    p = RuntimePaths.from_root(tmp_path)
    p.ensure()
    return p


def _e(card_id: str, kind: str, decision: str, **kw) -> HistoryEntry:
    base = dict(
        at="2026-05-22T10:00:00Z", card_id=card_id, kind=kind,
        decision=decision, reviewer_label="r", confidence=0.9,
        model_used="m",
    )
    base.update(kw)
    return HistoryEntry(**base)  # type: ignore[arg-type]


def test_history_path_is_under_signals(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    p = history_path(paths)
    assert p.name == HISTORY_FILENAME
    assert p.parent == paths.signals


def test_append_creates_file_and_returns_true(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    ok = append_entry(paths, _e("bH-01", "sibling_review", "approve"))
    assert ok is True
    assert history_path(paths).is_file()


def test_appends_accumulate(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    append_entry(paths, _e("bH-01", "sibling_review", "approve"))
    append_entry(paths, _e("bH-02", "sibling_review", "comment"))
    text = history_path(paths).read_text(encoding="utf-8")
    assert len(text.splitlines()) == 2


def test_read_history_returns_appended_entries(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    append_entry(paths, _e("bH-A", "sibling_review", "approve"))
    append_entry(paths, _e("bH-B", "amendment_review", "comment"))
    entries = read_history(paths)
    assert {e.card_id for e in entries} == {"bH-A", "bH-B"}


def test_read_history_filters_by_kind(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    append_entry(paths, _e("bH-A", "sibling_review", "approve"))
    append_entry(paths, _e("bH-B", "amendment_review", "comment"))
    append_entry(paths, _e("bH-C", "amendment_edit", "applied"))
    sibling = read_history(paths, kind="sibling_review")
    assert [e.card_id for e in sibling] == ["bH-A"]
    edits = read_history(paths, kind="amendment_edit")
    assert [e.card_id for e in edits] == ["bH-C"]


def test_read_history_filters_by_decision(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    append_entry(paths, _e("bH-A", "sibling_review", "approve"))
    append_entry(paths, _e("bH-B", "sibling_review", "comment"))
    approves = read_history(paths, decision="approve")
    assert [e.card_id for e in approves] == ["bH-A"]


def test_read_history_filters_by_since(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    append_entry(paths, _e("bH-EARLY", "sibling_review", "approve", at="2026-05-22T09:00:00Z"))
    append_entry(paths, _e("bH-LATE", "sibling_review", "approve", at="2026-05-22T11:00:00Z"))
    since = read_history(paths, since="2026-05-22T10:00:00Z")
    assert [e.card_id for e in since] == ["bH-LATE"]


def test_read_history_filters_by_card_id(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    append_entry(paths, _e("bH-A", "sibling_review", "approve"))
    append_entry(paths, _e("bH-B", "sibling_review", "approve"))
    one = read_history(paths, card_id="bH-A")
    assert len(one) == 1


def test_read_history_skips_malformed_lines(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    append_entry(paths, _e("bH-A", "sibling_review", "approve"))
    # Manually append a malformed line.
    with history_path(paths).open("a", encoding="utf-8") as fh:
        fh.write("{not valid json}\n")
        fh.write('\n')  # blank line
        fh.write('{"some_field": "value"}\n')  # missing required keys
    entries = read_history(paths)
    assert len(entries) == 1


def test_read_history_returns_empty_when_no_file(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert read_history(paths) == []


def test_aggregate_counts_by_kind_and_decision(tmp_path: Path) -> None:
    entries = [
        _e("a", "sibling_review", "approve", actual_cost_usd=0.01, input_tokens=10, output_tokens=20),
        _e("b", "sibling_review", "approve", actual_cost_usd=0.02, input_tokens=15, output_tokens=25),
        _e("c", "sibling_review", "comment", actual_cost_usd=0.005),
        _e("d", "amendment_review", "approve"),
        _e("e", "amendment_edit", "applied", actual_cost_usd=0.03, input_tokens=40, output_tokens=10),
    ]
    summary = aggregate(entries)
    assert summary["total_entries"] == 5
    assert summary["by_kind"] == {
        "sibling_review": 3,
        "amendment_review": 1,
        "amendment_edit": 1,
    }
    assert summary["by_decision"] == {"approve": 3, "comment": 1, "applied": 1}
    assert summary["by_kind_decision"]["sibling_review"]["approve"] == 2
    assert summary["total_input_tokens"] == 65
    assert summary["total_output_tokens"] == 55
    assert summary["total_cost_usd"] == 0.065


def test_aggregate_handles_empty() -> None:
    summary = aggregate([])
    assert summary["total_entries"] == 0
    assert summary["total_cost_usd"] == 0.0


def test_entry_to_jsonl_round_trips(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    e = _e("bH-RT", "amendment_edit", "applied", ac_index=2,
           amendment_reason="moved file", actual_cost_usd=0.001,
           input_tokens=300, output_tokens=70, pr_url=None)
    append_entry(paths, e)
    out = read_history(paths)
    assert len(out) == 1
    rt = out[0]
    assert rt.card_id == "bH-RT"
    assert rt.kind == "amendment_edit"
    assert rt.ac_index == 2
    assert rt.amendment_reason == "moved file"
    assert rt.actual_cost_usd == 0.001
    assert rt.input_tokens == 300
