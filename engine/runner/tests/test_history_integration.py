"""End-to-end: reviewers append to the JSONL history when they decide."""
from __future__ import annotations

from pathlib import Path

from cards_runner.common.project_config import ReviewerConfig
from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.ac_editor import AmendmentEdit
from cards_runner.daemon.amendment_editor_client import StaticAmendmentEditorClient
from cards_runner.daemon.amendment_reviewer import run_amendment_reviews
from cards_runner.daemon.reviewer_cost import ReviewerUsage
from cards_runner.daemon.reviewer_history import aggregate, read_history
from cards_runner.daemon.sibling_reviewer import (
    ReviewerDecision,
    StaticSiblingReviewerClient,
    run_sibling_reviews,
)
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text
from tests.test_sibling_reviewer import _FakeGh, _insert_review_card


def test_sibling_review_appends_history_entry(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo, card_id="bHI-01", pr_url="https://x/1", points=3)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    gh = _FakeGh()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="approve", reasoning="LGTM", confidence=0.9,
            model_used="claude-sonnet-4-6",
            usage=ReviewerUsage(input_tokens=200, output_tokens=50, cost_usd=0.001, model_id="claude-sonnet-4-6"),
        ),
    )
    run_sibling_reviews(
        repo=repo, gh=gh,
        cfg=DaemonConfig(todo_root=tmp_path, sibling_reviewer_enabled=True),
        paths=paths, reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True, label="sib-1"),
    )
    entries = read_history(paths)
    assert len(entries) == 1
    e = entries[0]
    assert e.card_id == "bHI-01"
    assert e.kind == "sibling_review"
    assert e.decision == "approve"
    assert e.reviewer_label == "sib-1"
    assert e.pr_url == "https://x/1"
    assert e.actual_cost_usd == 0.001
    assert e.input_tokens == 200


def test_amendment_review_appends_history_entry(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    text = _card_text("bHI-02", points=2)
    record = card_text_to_record(text)
    record.status = CardStatus.AMENDMENTS.value
    record.body_md = record.body_md.rstrip() + """

```yaml
change_request:
  ac_index: 0
  reason: x
```
"""
    repo.create_card(record)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="comment", reasoning="x", confidence=0.3,
        ),
    )
    run_amendment_reviews(
        repo=repo,
        cfg=DaemonConfig(todo_root=tmp_path, amendment_reviewer_enabled=True),
        paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True),
    )
    entries = read_history(paths, kind="amendment_review")
    assert len(entries) == 1
    assert entries[0].card_id == "bHI-02"
    assert entries[0].decision == "comment"


def test_amendment_edit_appends_separate_history_entry(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    text = _card_text("bHI-03", points=2)
    record = card_text_to_record(text)
    record.status = CardStatus.AMENDMENTS.value
    record.body_md = record.body_md.rstrip() + """

```yaml
change_request:
  ac_index: 0
  reason: x
```
"""
    repo.create_card(record)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    reviewer = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="approve", reasoning="ok", confidence=0.95),
    )
    editor = StaticAmendmentEditorClient(
        default=AmendmentEdit(
            ac_index=0, description="amended", check_type="file_exists",
            check_fields={"path": "x.md"},
            amendment_reason="renamed", confidence=0.95,
            model_used="claude-sonnet-4-6",
            input_tokens=300, output_tokens=70, actual_cost_usd=0.002,
        ),
    )
    run_amendment_reviews(
        repo=repo,
        cfg=DaemonConfig(todo_root=tmp_path, amendment_reviewer_enabled=True),
        paths=paths,
        reviewer_client=reviewer,
        reviewer_config=ReviewerConfig(enabled=True, auto_edit_ac=True),
        editor_client=editor,
    )
    entries = read_history(paths)
    # Two entries: amendment_review (approve) + amendment_edit (applied)
    kinds = sorted(e.kind for e in entries)
    assert kinds == ["amendment_edit", "amendment_review"]
    edit_entry = next(e for e in entries if e.kind == "amendment_edit")
    assert edit_entry.ac_index == 0
    assert edit_entry.actual_cost_usd == 0.002
    assert edit_entry.amendment_reason == "renamed"


def test_aggregate_across_appended_entries(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo, card_id="bHI-04", pr_url="https://x/4", points=3)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    gh = _FakeGh()
    for decision_label in ("approve", "request_changes", "comment"):
        client = StaticSiblingReviewerClient(
            default=ReviewerDecision(
                decision=decision_label,
                reasoning="x", confidence=0.9,
                usage=ReviewerUsage(input_tokens=100, output_tokens=20, cost_usd=0.0005, model_id="m"),
            ),
        )
        # Each iteration's review re-files since the marker exists; we
        # blow away the marker between iterations to force a re-review.
        from cards_runner.daemon.sibling_reviewer import sibling_review_marker_path

        marker = sibling_review_marker_path(paths, "bHI-04")
        if marker.exists():
            marker.unlink()
        run_sibling_reviews(
            repo=repo, gh=gh,
            cfg=DaemonConfig(todo_root=tmp_path, sibling_reviewer_enabled=True),
            paths=paths, reviewer_client=client,
            reviewer_config=ReviewerConfig(enabled=True),
        )
    summary = aggregate(read_history(paths))
    assert summary["total_entries"] == 3
    assert summary["by_decision"]["approve"] == 1
    assert summary["total_input_tokens"] == 300
