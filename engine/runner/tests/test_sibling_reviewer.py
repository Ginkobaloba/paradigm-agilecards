"""Tests for `cards_runner.daemon.sibling_reviewer`."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cards_runner.common.project_config import ReviewerConfig
from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.pr_lifecycle import GhCallResult
from cards_runner.daemon.sibling_reviewer import (
    ReviewerDecision,
    StaticSiblingReviewerClient,
    _parse_decision,
    run_sibling_reviews,
    sibling_review_marker_path,
)
from cards_runner.store import CardStatus, EventType
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text


@dataclass
class _FakeGh:
    diff_results: list[GhCallResult] = field(default_factory=list)
    review_results: list[GhCallResult] = field(default_factory=list)
    merge_results: list[GhCallResult] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def is_available(self) -> bool: return True
    def push(self, *a: Any, **k: Any) -> GhCallResult: return GhCallResult(ok=True)
    def open_pr(self, *a: Any, **k: Any) -> GhCallResult: return GhCallResult(ok=True)
    def view_pr(self, *a: Any, **k: Any) -> GhCallResult: return GhCallResult(ok=True)

    def pr_diff(self, *, identifier: str, worktree: Path | None = None) -> GhCallResult:
        self.calls.append(("pr_diff", {"identifier": identifier}))
        return self.diff_results.pop(0) if self.diff_results else GhCallResult(ok=True, stdout="diff")

    def pr_review(
        self,
        *,
        identifier: str,
        decision: str,
        body: str,
        worktree: Path | None = None,
    ) -> GhCallResult:
        self.calls.append(("pr_review", {"identifier": identifier, "decision": decision, "body": body}))
        return self.review_results.pop(0) if self.review_results else GhCallResult(ok=True)

    def merge_pr(
        self,
        worktree: Path,
        *,
        identifier: str,
        strategy: str = "squash",
    ) -> GhCallResult:
        self.calls.append(("merge_pr", {"identifier": identifier, "strategy": strategy}))
        return self.merge_results.pop(0) if self.merge_results else GhCallResult(ok=True)


def _insert_review_card(
    repo: SqliteRepository,
    *,
    card_id: str = "bSR-01",
    points: int = 3,
    pr_url: str = "https://github.com/x/y/pull/77",
) -> None:
    record = card_text_to_record(_card_text(card_id, points=points))
    record.status = CardStatus.BLOCKED.value
    record.merge_status = "requires_review"
    record.pr_url = pr_url
    repo.create_card(record)


def _cfg(tmp_path: Path, *, enabled: bool = True) -> DaemonConfig:
    return DaemonConfig(
        todo_root=tmp_path,
        sibling_reviewer_enabled=enabled,
        skip_worktree=True,
    )


def test_disabled_via_host_knob_is_noop(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo)
    gh = _FakeGh()
    cfg = _cfg(tmp_path, enabled=False)
    outcomes = run_sibling_reviews(
        repo=repo, gh=gh, cfg=cfg,
        paths=RuntimePaths.from_root(tmp_path),
        reviewer_client=StaticSiblingReviewerClient(),
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes == []


def test_disabled_via_project_knob_is_noop(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo)
    cfg = _cfg(tmp_path, enabled=True)
    outcomes = run_sibling_reviews(
        repo=repo, gh=_FakeGh(), cfg=cfg,
        paths=RuntimePaths.from_root(tmp_path),
        reviewer_client=StaticSiblingReviewerClient(),
        reviewer_config=ReviewerConfig(enabled=False),
    )
    assert outcomes == []


def test_no_pr_url_skips(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo, pr_url="")  # type: ignore[arg-type]
    outcomes = run_sibling_reviews(
        repo=repo, gh=_FakeGh(), cfg=_cfg(tmp_path),
        paths=RuntimePaths.from_root(tmp_path),
        reviewer_client=StaticSiblingReviewerClient(),
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].action == "skipped_no_pr"


def test_diff_failure_skips(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo)
    gh = _FakeGh(diff_results=[GhCallResult(ok=False, reason="404")])
    outcomes = run_sibling_reviews(
        repo=repo, gh=gh, cfg=_cfg(tmp_path),
        paths=RuntimePaths.from_root(tmp_path),
        reviewer_client=StaticSiblingReviewerClient(),
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].action == "skipped_gh"


def test_approve_path_writes_marker_and_fires_merge(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo)
    gh = _FakeGh()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="approve", reasoning="LGTM", confidence=0.9,
            model_used="claude-sonnet-4-6",
        )
    )
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    outcomes = run_sibling_reviews(
        repo=repo, gh=gh, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True, label="ag-1"),
    )
    assert outcomes[0].action == "reviewed"
    assert outcomes[0].decision == "approve"
    # gh got the diff, then the review (--approve), then the merge.
    kinds = [c[0] for c in gh.calls]
    assert kinds == ["pr_diff", "pr_review", "merge_pr"]
    assert gh.calls[1][1]["decision"] == "approve"
    # Marker is on disk and idempotent.
    marker = sibling_review_marker_path(paths, "bSR-01")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["decision"] == "approve"
    assert payload["reviewer_label"] == "ag-1"
    assert payload["pr_url"] == "https://github.com/x/y/pull/77"


def test_marker_skips_subsequent_pass(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="comment", reasoning="x", confidence=0.0),
    )
    cfg = _cfg(tmp_path)
    rcfg = ReviewerConfig(enabled=True)
    first = run_sibling_reviews(
        repo=repo, gh=_FakeGh(), cfg=cfg, paths=paths,
        reviewer_client=client, reviewer_config=rcfg,
    )
    assert first[0].action == "reviewed"
    # Second pass: marker present, no new client call.
    client.calls.clear()
    second = run_sibling_reviews(
        repo=repo, gh=_FakeGh(), cfg=cfg, paths=paths,
        reviewer_client=client, reviewer_config=rcfg,
    )
    assert second[0].action == "skipped_existing"
    assert client.calls == []


def test_request_changes_does_not_fire_merge(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo)
    gh = _FakeGh()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="request_changes", reasoning="no", confidence=0.9),
    )
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    outcomes = run_sibling_reviews(
        repo=repo, gh=gh, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=client, reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].decision == "request_changes"
    assert all(c[0] != "merge_pr" for c in gh.calls)


def test_event_emitted_for_review(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_review_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="comment", reasoning="meh", confidence=0.1),
    )
    run_sibling_reviews(
        repo=repo, gh=_FakeGh(), cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=client, reviewer_config=ReviewerConfig(enabled=True),
    )
    events = repo.list_events("bSR-01")
    assert any(
        e.type == EventType.VERIFIED.value
        and (e.payload or {}).get("source") == "sibling_reviewer"
        for e in events
    )


def test_parse_decision_yaml_block() -> None:
    text = "```yaml\ndecision: approve\nconfidence: 0.95\nreasoning: ok\n```"
    parsed = _parse_decision(text)
    assert parsed.decision == "approve"
    assert parsed.confidence == 0.95


def test_parse_decision_bare_yaml() -> None:
    text = "decision: comment\nconfidence: 0.4\nreasoning: maybe"
    parsed = _parse_decision(text)
    assert parsed.decision == "comment"


def test_parse_decision_unknown_decision_falls_back() -> None:
    text = "decision: wat\nconfidence: 0.5\nreasoning: rly"
    parsed = _parse_decision(text)
    assert parsed.decision == "comment"


def test_parse_decision_garbage_returns_comment() -> None:
    parsed = _parse_decision(":::not yaml:::")
    assert parsed.decision == "comment"
    assert parsed.confidence == 0.0
