"""Tests for `cards_runner.daemon.unblocker`.

Drives the chunk-5 poll-for-merged unblocker against a fake gh runner.
Covers: no-op when disabled; skip rules (no PR URL, gh failure, unknown
state, still pending); the merged path that promotes a blocked card to
done with a `merged` event.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest  # noqa: F401 - parametrize / fixtures.

from cards_runner.common.types import DaemonConfig
from cards_runner.daemon.pr_lifecycle import GhCallResult
from cards_runner.daemon.unblocker import (
    UnblockDecision,
    split_decisions,
    unblock_merged_cards,
)
from cards_runner.store import CardStatus, EventType
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text


@dataclass
class _FakeGh:
    view_results: list[GhCallResult] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def is_available(self) -> bool: return True
    def push(self, *args: Any, **kwargs: Any) -> GhCallResult: return GhCallResult(ok=True)
    def open_pr(self, *args: Any, **kwargs: Any) -> GhCallResult: return GhCallResult(ok=True)
    def merge_pr(self, *args: Any, **kwargs: Any) -> GhCallResult: return GhCallResult(ok=True)

    def view_pr(
        self,
        *,
        identifier: str,
        fields: tuple[str, ...] = ("state", "mergedAt", "url"),
        worktree: Path | None = None,
    ) -> GhCallResult:
        self.calls.append({"identifier": identifier, "fields": fields})
        return self.view_results.pop(0) if self.view_results else GhCallResult(ok=True)

    def pr_diff(self, *args: Any, **kwargs: Any) -> GhCallResult: return GhCallResult(ok=True)
    def pr_review(self, *args: Any, **kwargs: Any) -> GhCallResult: return GhCallResult(ok=True)


def _insert_blocked_with_pr(
    repo: SqliteRepository,
    *,
    card_id: str,
    merge_status: str,
    pr_url: str | None = "https://github.com/x/y/pull/7",
) -> None:
    record = card_text_to_record(_card_text(card_id, points=2))
    record.status = CardStatus.BLOCKED.value
    record.merge_status = merge_status
    record.pr_url = pr_url
    repo.create_card(record)


def _cfg(enabled: bool, tmp_path: Path) -> DaemonConfig:
    return DaemonConfig(
        todo_root=tmp_path,
        pr_unblock_enabled=enabled,
        skip_worktree=True,
    )


def test_disabled_returns_empty(repo: SqliteRepository, tmp_path: Path) -> None:
    _insert_blocked_with_pr(repo, card_id="bU-01", merge_status="open")
    decisions = unblock_merged_cards(
        repo=repo, gh=_FakeGh(), cfg=_cfg(False, tmp_path)
    )
    assert decisions == []


def test_skipped_no_url(repo: SqliteRepository, tmp_path: Path) -> None:
    _insert_blocked_with_pr(repo, card_id="bU-02", merge_status="open", pr_url=None)
    gh = _FakeGh()
    decisions = unblock_merged_cards(
        repo=repo, gh=gh, cfg=_cfg(True, tmp_path)
    )
    assert len(decisions) == 1
    assert decisions[0].action == "skipped_no_url"
    assert gh.calls == []  # never asked gh.


def test_skipped_gh_failure(repo: SqliteRepository, tmp_path: Path) -> None:
    _insert_blocked_with_pr(repo, card_id="bU-03", merge_status="open")
    gh = _FakeGh(view_results=[GhCallResult(ok=False, reason="404")])
    decisions = unblock_merged_cards(
        repo=repo, gh=gh, cfg=_cfg(True, tmp_path)
    )
    assert decisions[0].action == "skipped_gh_failure"
    # The card stayed in blocked.
    assert repo.get_card("bU-03").status == CardStatus.BLOCKED.value


def test_still_pending_when_state_open(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_blocked_with_pr(repo, card_id="bU-04", merge_status="open")
    gh = _FakeGh(
        view_results=[GhCallResult(ok=True, parsed={"state": "OPEN"})],
    )
    decisions = unblock_merged_cards(
        repo=repo, gh=gh, cfg=_cfg(True, tmp_path)
    )
    assert decisions[0].action == "still_pending"
    assert decisions[0].pr_state == "open"


def test_merged_transitions_to_done(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_blocked_with_pr(repo, card_id="bU-05", merge_status="open")
    gh = _FakeGh(view_results=[
        GhCallResult(ok=True, parsed={
            "state": "MERGED", "mergedAt": "2026-05-20T12:00:00Z",
        }),
    ])
    decisions = unblock_merged_cards(
        repo=repo, gh=gh, cfg=_cfg(True, tmp_path), actor_id="test-unblocker"
    )
    assert decisions[0].action == "unblocked"
    card = repo.get_card("bU-05")
    assert card.status == CardStatus.DONE.value
    assert card.merge_status == "merged"
    # And the merged event landed.
    events = repo.list_events("bU-05")
    merged_events = [e for e in events if e.type == EventType.MERGED.value]
    assert merged_events
    last = merged_events[-1]
    assert last.payload["pr_url"] == "https://github.com/x/y/pull/7"
    assert last.payload["merged_at"] == "2026-05-20T12:00:00Z"


def test_merged_requires_review_also_unblocks(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_blocked_with_pr(
        repo, card_id="bU-06", merge_status="requires_review"
    )
    gh = _FakeGh(view_results=[
        GhCallResult(ok=True, parsed={"state": "merged"}),
    ])
    decisions = unblock_merged_cards(
        repo=repo, gh=gh, cfg=_cfg(True, tmp_path)
    )
    assert decisions[0].action == "unblocked"


def test_blocked_conflict_is_not_visited(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    """`merge_status=conflict` is a human-fix state; the unblocker
    shouldn't even poll it."""
    _insert_blocked_with_pr(
        repo, card_id="bU-07", merge_status="conflict"
    )
    gh = _FakeGh(view_results=[GhCallResult(ok=True, parsed={"state": "OPEN"})])
    decisions = unblock_merged_cards(
        repo=repo, gh=gh, cfg=_cfg(True, tmp_path)
    )
    assert decisions == []
    assert gh.calls == []


def test_split_decisions_groups_by_action() -> None:
    grouped = split_decisions([
        UnblockDecision(card_id="a", action="unblocked"),
        UnblockDecision(card_id="b", action="still_pending"),
        UnblockDecision(card_id="c", action="unblocked"),
    ])
    assert len(grouped["unblocked"]) == 2
    assert len(grouped["still_pending"]) == 1
