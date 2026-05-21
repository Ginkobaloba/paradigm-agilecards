"""Tests for `cards_runner.daemon.amendment_reviewer`."""
from __future__ import annotations

import json
from pathlib import Path

from cards_runner.common.project_config import ReviewerConfig
from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.amendment_reviewer import (
    _append_change_request_decision,
    amendment_review_marker_path,
    extract_change_request_block,
    run_amendment_reviews,
)
from cards_runner.daemon.sibling_reviewer import (
    ReviewerDecision,
    StaticSiblingReviewerClient,
)
from cards_runner.store import CardStatus, EventType
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text


CHANGE_REQUEST_SNIPPET = """
## Change request

```yaml
change_request:
  ac_index: 0
  reason: "AC item is impossible as written"
  proposed_replacement: "Use foo instead of bar"
```
""".strip()


def _insert_amend_card(
    repo: SqliteRepository,
    *,
    card_id: str = "bAM-01",
    include_change_request: bool = True,
) -> None:
    text = _card_text(card_id, points=2)
    record = card_text_to_record(text)
    record.status = CardStatus.AMENDMENTS.value
    if include_change_request:
        record.body_md = record.body_md.rstrip() + "\n\n" + CHANGE_REQUEST_SNIPPET + "\n"
    repo.create_card(record)


def _cfg(tmp_path: Path, *, enabled: bool = True) -> DaemonConfig:
    return DaemonConfig(
        todo_root=tmp_path,
        amendment_reviewer_enabled=enabled,
        skip_worktree=True,
    )


def test_disabled_via_host_knob_is_noop(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path, enabled=False),
        paths=RuntimePaths.from_root(tmp_path),
        reviewer_client=StaticSiblingReviewerClient(),
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes == []


def test_disabled_via_project_knob_is_noop(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path),
        paths=RuntimePaths.from_root(tmp_path),
        reviewer_client=StaticSiblingReviewerClient(),
        reviewer_config=ReviewerConfig(enabled=False),
    )
    assert outcomes == []


def test_missing_change_request_skips(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo, include_change_request=False)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=StaticSiblingReviewerClient(),
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].action == "skipped_no_change_request"


def test_approve_routes_to_blocked_amendment_approved(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="approve", reasoning="legit", confidence=0.9,
            model_used="claude-sonnet-4-6",
        )
    )
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True, label="amend-1"),
    )
    assert outcomes[0].action == "reviewed_approve"
    card = repo.get_card("bAM-01")
    assert card.status == CardStatus.BLOCKED.value
    assert card.merge_status == "amendment_approved"
    # Marker is on disk.
    marker = amendment_review_marker_path(paths, "bAM-01")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["decision"] == "approve"
    assert payload["reviewer_label"] == "amend-1"


def test_deny_routes_to_active_and_appends_decision_block(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="request_changes",
            reasoning="AC was correct as written",
            confidence=0.95,
        )
    )
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True, label="amend-2"),
    )
    assert outcomes[0].action == "reviewed_deny"
    card = repo.get_card("bAM-01")
    assert card.status == CardStatus.ACTIVE.value
    # Body has the new change_request_decision: block but still carries the
    # original change_request: block (audit trail).
    assert "change_request_decision:" in card.body_md
    assert "change_request:" in card.body_md
    assert "outcome: denied" in card.body_md
    assert "decided_by: amend-2" in card.body_md


def test_comment_leaves_card_in_amendments(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="comment", reasoning="not sure", confidence=0.3),
    )
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].action == "reviewed_comment"
    card = repo.get_card("bAM-01")
    assert card.status == CardStatus.AMENDMENTS.value


def test_existing_marker_skips(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="comment", reasoning="x", confidence=0.0),
    )
    rcfg = ReviewerConfig(enabled=True)
    first = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=client, reviewer_config=rcfg,
    )
    assert first[0].action == "reviewed_comment"
    client.calls.clear()
    second = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=client, reviewer_config=rcfg,
    )
    assert second[0].action == "skipped_existing"
    assert client.calls == []


def test_event_emitted_for_amendment_review(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="approve", reasoning="ok", confidence=0.9),
    )
    run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=client, reviewer_config=ReviewerConfig(enabled=True),
    )
    events = repo.list_events("bAM-01")
    amend_events = [
        e for e in events
        if e.type == EventType.AMENDED.value
        and (e.payload or {}).get("source") == "amendment_reviewer"
    ]
    assert amend_events


def test_extract_change_request_block_fenced() -> None:
    body = CHANGE_REQUEST_SNIPPET
    block = extract_change_request_block(body)
    assert "change_request:" in block
    assert "ac_index" in block


def test_extract_change_request_block_unfenced() -> None:
    body = "change_request:\n  ac_index: 1\n"
    assert "change_request:" in extract_change_request_block(body)


def test_extract_change_request_block_absent() -> None:
    assert extract_change_request_block("no block here") == ""


def test_append_change_request_decision_multiline_reasoning() -> None:
    appended = _append_change_request_decision(
        "body here",
        decision=ReviewerDecision(
            decision="request_changes",
            reasoning="line one\nline two",
            confidence=0.42,
        ),
        reviewer_config=ReviewerConfig(label="r"),
        outcome="denied",
    )
    assert "line one" in appended
    assert "line two" in appended
    assert "outcome: denied" in appended
