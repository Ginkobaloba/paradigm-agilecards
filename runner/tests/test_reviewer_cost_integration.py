"""End-to-end tests for reviewer cost attribution (chunk 6b)."""
from __future__ import annotations

import json
from pathlib import Path

from cards_runner.common.project_config import ReviewerConfig
from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.ac_editor import AmendmentEdit
from cards_runner.daemon.amendment_editor_client import (
    StaticAmendmentEditorClient,
)
from cards_runner.daemon.amendment_reviewer import (
    amendment_review_marker_path,
    run_amendment_reviews,
)
from cards_runner.daemon.reviewer_cost import ReviewerUsage
from cards_runner.daemon.sibling_reviewer import (
    ReviewerDecision,
    StaticSiblingReviewerClient,
    run_sibling_reviews,
    sibling_review_marker_path,
)
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text
from tests.test_sibling_reviewer import _FakeGh, _insert_review_card


def _insert_review_card_with_cap(
    repo: SqliteRepository,
    *,
    card_id: str,
    pr_url: str,
    points: int = 3,
    cost_cap_usd: float | None = None,
) -> None:
    record = card_text_to_record(_card_text(card_id, points=points))
    record.status = CardStatus.BLOCKED.value
    record.merge_status = "requires_review"
    record.pr_url = pr_url
    if cost_cap_usd is not None:
        record.frontmatter_extra["cost_cap_usd"] = cost_cap_usd
    repo.create_card(record)


# ---- sibling reviewer cost attribution ------------------------------


def test_sibling_reviewer_attributes_tokens_to_card(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    """A reviewer decision with usage increments the card's actual_tokens."""
    _insert_review_card(repo, card_id="bSC-01", pr_url="https://x/1", points=3)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    gh = _FakeGh()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="comment",
            reasoning="x",
            confidence=0.5,
            usage=ReviewerUsage(
                input_tokens=500, output_tokens=100, cost_usd=0.001,
                model_id="claude-haiku-4-5-20251001",
            ),
        ),
    )
    outcomes = run_sibling_reviews(
        repo=repo, gh=gh,
        cfg=DaemonConfig(todo_root=tmp_path, sibling_reviewer_enabled=True, skip_worktree=True),
        paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].action == "reviewed"
    refreshed = repo.get_card("bSC-01")
    assert refreshed.actual_tokens == 600
    marker = sibling_review_marker_path(paths, "bSC-01")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["cost"]["input_tokens"] == 500
    assert payload["cost"]["output_tokens"] == 100
    assert payload["cost"]["card_actual_tokens_after"] == 600


def test_sibling_reviewer_no_usage_zero_attribution(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    """When the reviewer reports no usage (static client default), the
    card's actual_tokens is unchanged but a marker still lands."""
    _insert_review_card(repo, card_id="bSC-02", pr_url="https://x/2", points=3)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    gh = _FakeGh()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="comment", reasoning="x", confidence=0.0),
    )
    outcomes = run_sibling_reviews(
        repo=repo, gh=gh,
        cfg=DaemonConfig(todo_root=tmp_path, sibling_reviewer_enabled=True, skip_worktree=True),
        paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].action == "reviewed"
    marker_payload = json.loads(
        sibling_review_marker_path(paths, "bSC-02").read_text(encoding="utf-8")
    )
    assert marker_payload["cost"]["input_tokens"] == 0


def test_sibling_reviewer_skips_when_card_cap_would_breach(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    """A card with a tiny cost_cap_usd causes the reviewer call to skip
    pre-emptively."""
    _insert_review_card_with_cap(
        repo, card_id="bSC-03", pr_url="https://x/3", points=3,
        cost_cap_usd=0.0001,  # absurdly tight cap
    )
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    gh = _FakeGh()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="approve", reasoning="x", confidence=0.9),
    )
    outcomes = run_sibling_reviews(
        repo=repo, gh=gh,
        cfg=DaemonConfig(todo_root=tmp_path, sibling_reviewer_enabled=True, skip_worktree=True),
        paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].action == "skipped_cost_cap"
    assert "cost_cap_usd" in outcomes[0].reason
    # No marker written -- next tick re-evaluates if the cap is raised.
    assert not sibling_review_marker_path(paths, "bSC-03").exists()


def test_sibling_reviewer_skips_when_reviewer_cap_would_breach(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    """A reviewer with a tiny cost_cap_usd causes its own call to skip."""
    _insert_review_card(repo, card_id="bSC-04", pr_url="https://x/4", points=3)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    gh = _FakeGh()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="approve", reasoning="x", confidence=0.9),
    )
    outcomes = run_sibling_reviews(
        repo=repo, gh=gh,
        cfg=DaemonConfig(todo_root=tmp_path, sibling_reviewer_enabled=True, skip_worktree=True),
        paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True, cost_cap_usd=0.00001),
    )
    assert outcomes[0].action == "skipped_cost_cap"
    assert "reviewer cost cap" in outcomes[0].reason


# ---- amendment reviewer cost attribution -----------------------------


CHANGE_REQUEST_SNIPPET = """
## Change request

```yaml
change_request:
  ac_index: 0
  reason: "x"
  proposed_replacement: "y"
```
""".strip()


def _insert_amend_card(
    repo: SqliteRepository,
    *,
    card_id: str = "bAC-01",
    cost_cap_usd: float | None = None,
) -> None:
    text = _card_text(card_id, points=2)
    record = card_text_to_record(text)
    record.status = CardStatus.AMENDMENTS.value
    record.body_md = record.body_md.rstrip() + "\n\n" + CHANGE_REQUEST_SNIPPET + "\n"
    if cost_cap_usd is not None:
        record.frontmatter_extra["cost_cap_usd"] = cost_cap_usd
    repo.create_card(record)


def test_amendment_reviewer_attributes_tokens(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo, card_id="bAC-01")
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="comment", reasoning="x", confidence=0.3,
            usage=ReviewerUsage(
                input_tokens=200, output_tokens=80, cost_usd=0.0005,
                model_id="claude-haiku-4-5-20251001",
            ),
        ),
    )
    outcomes = run_amendment_reviews(
        repo=repo,
        cfg=DaemonConfig(
            todo_root=tmp_path, amendment_reviewer_enabled=True,
            skip_worktree=True,
        ),
        paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].action == "reviewed_comment"
    refreshed = repo.get_card("bAC-01")
    assert refreshed.actual_tokens == 280
    marker = amendment_review_marker_path(paths, "bAC-01")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["cost"]["input_tokens"] == 200
    assert payload["cost"]["card_actual_tokens_after"] == 280


def test_amendment_reviewer_skips_on_card_cap(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo, card_id="bAC-02", cost_cap_usd=0.00001)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    client = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="approve", reasoning="x", confidence=0.9),
    )
    outcomes = run_amendment_reviews(
        repo=repo,
        cfg=DaemonConfig(
            todo_root=tmp_path, amendment_reviewer_enabled=True,
            skip_worktree=True,
        ),
        paths=paths,
        reviewer_client=client,
        reviewer_config=ReviewerConfig(enabled=True),
    )
    assert outcomes[0].action == "skipped_cost_cap"
    assert not amendment_review_marker_path(paths, "bAC-02").exists()


def test_amendment_editor_cost_attributed_too(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    """The auto-edit path attributes BOTH reviewer + editor tokens."""
    _insert_amend_card(repo, card_id="bAC-03")
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    reviewer = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="approve", reasoning="LGTM", confidence=0.95,
            usage=ReviewerUsage(
                input_tokens=100, output_tokens=50, cost_usd=0.0001,
                model_id="claude-haiku-4-5-20251001",
            ),
        ),
    )
    editor = StaticAmendmentEditorClient(
        default=AmendmentEdit(
            ac_index=0, description="amended", check_type="file_exists",
            check_fields={"path": "x.md"},
            amendment_reason="renamed", confidence=0.99,
            model_used="claude-haiku-4-5-20251001",
            input_tokens=300, output_tokens=70,
            actual_cost_usd=0.001,
        ),
    )
    outcomes = run_amendment_reviews(
        repo=repo,
        cfg=DaemonConfig(
            todo_root=tmp_path, amendment_reviewer_enabled=True,
            skip_worktree=True,
        ),
        paths=paths,
        reviewer_client=reviewer,
        reviewer_config=ReviewerConfig(enabled=True, auto_edit_ac=True),
        editor_client=editor,
    )
    assert outcomes[0].action == "reviewed_approve_edited"
    refreshed = repo.get_card("bAC-03")
    # reviewer 150 + editor 370 = 520
    assert refreshed.actual_tokens == 520
    marker = amendment_review_marker_path(paths, "bAC-03")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["cost"]["input_tokens"] == 100  # reviewer's contribution
    assert payload["auto_edit"]["cost"]["input_tokens"] == 300  # editor's contribution
    assert payload["auto_edit"]["cost"]["card_actual_tokens_after"] == 520


def test_amendment_editor_cost_attributed_even_on_low_confidence_fallback(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    """The editor was called and paid; attribute the tokens even though
    the splice didn't happen."""
    _insert_amend_card(repo, card_id="bAC-04")
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    reviewer = StaticSiblingReviewerClient(
        default=ReviewerDecision(decision="approve", reasoning="LGTM", confidence=0.95),
    )
    editor = StaticAmendmentEditorClient(
        default=AmendmentEdit(
            ac_index=0, description="amended", check_type="file_exists",
            check_fields={"path": "x.md"},
            amendment_reason="x", confidence=0.5,  # below floor
            input_tokens=300, output_tokens=70, actual_cost_usd=0.001,
        ),
    )
    outcomes = run_amendment_reviews(
        repo=repo,
        cfg=DaemonConfig(
            todo_root=tmp_path, amendment_reviewer_enabled=True,
            skip_worktree=True,
        ),
        paths=paths,
        reviewer_client=reviewer,
        reviewer_config=ReviewerConfig(
            enabled=True, auto_edit_ac=True,
            auto_edit_confidence_floor=0.85,
        ),
        editor_client=editor,
    )
    assert outcomes[0].action == "reviewed_approve"  # fell back to blocked
    refreshed = repo.get_card("bAC-04")
    assert refreshed.actual_tokens == 370  # editor's tokens still attributed
