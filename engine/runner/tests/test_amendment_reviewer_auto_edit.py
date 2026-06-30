"""Tests for the chunk 6a `auto_edit_ac` flow in amendment_reviewer."""
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
    card_id: str = "bAMA-01",
    include_change_request: bool = True,
) -> None:
    text = _card_text(card_id, points=2)
    record = card_text_to_record(text)
    record.status = CardStatus.AMENDMENTS.value
    if include_change_request:
        record.body_md = (
            record.body_md.rstrip() + "\n\n" + CHANGE_REQUEST_SNIPPET + "\n"
        )
    repo.create_card(record)


def _cfg(tmp_path: Path) -> DaemonConfig:
    return DaemonConfig(
        todo_root=tmp_path,
        amendment_reviewer_enabled=True,
        skip_worktree=True,
    )


def _approving_reviewer(confidence: float = 0.95) -> StaticSiblingReviewerClient:
    return StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="approve", reasoning="LGTM", confidence=confidence,
            model_used="claude-sonnet-4-6",
        ),
    )


# ---- auto_edit_ac=False: chunk 5 behavior preserved ------------------


def test_auto_edit_off_still_routes_approve_to_blocked(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    editor = StaticAmendmentEditorClient(
        default=AmendmentEdit(
            ac_index=0, description="should not be used",
            check_type="file_exists", amendment_reason="x", confidence=0.99,
        ),
    )
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=_approving_reviewer(),
        reviewer_config=ReviewerConfig(enabled=True, auto_edit_ac=False),
        editor_client=editor,
    )
    assert outcomes[0].action == "reviewed_approve"
    assert outcomes[0].decision == "approve"
    card = repo.get_card("bAMA-01")
    assert card.status == CardStatus.BLOCKED.value
    assert card.merge_status == "amendment_approved"
    # The editor was never invoked.
    assert editor.calls == []


def test_no_editor_client_falls_back_to_blocked(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    """auto_edit_ac=True but editor_client=None == chunk 5 behavior."""
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=_approving_reviewer(),
        reviewer_config=ReviewerConfig(enabled=True, auto_edit_ac=True),
        editor_client=None,
    )
    assert outcomes[0].action == "reviewed_approve"
    card = repo.get_card("bAMA-01")
    assert card.status == CardStatus.BLOCKED.value


# ---- auto_edit_ac=True: happy path -----------------------------------


def test_auto_edit_happy_path_routes_to_backlog_with_amended_ac(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    edit = AmendmentEdit(
        ac_index=0,
        description="Smoke (amended)",
        check_type="file_exists",
        check_fields={"path": "READMEv2.md"},
        amendment_reason="renamed README on disk",
        confidence=0.99,
        model_used="claude-sonnet-4-6",
    )
    editor = StaticAmendmentEditorClient(default=edit)
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=_approving_reviewer(),
        reviewer_config=ReviewerConfig(
            enabled=True, auto_edit_ac=True, label="amend-edit-1",
        ),
        editor_client=editor,
    )
    assert outcomes[0].action == "reviewed_approve_edited"
    assert outcomes[0].decision == "approve"
    card = repo.get_card("bAMA-01")
    assert card.status == CardStatus.BACKLOG.value
    assert card.merge_status == "pending"
    # The amended AC + provenance is in the body.
    assert "Smoke (amended)" in card.body_md
    assert "READMEv2.md" in card.body_md
    assert "amended_by: amend-edit-1" in card.body_md
    assert "amendment_reason: renamed README on disk" in card.body_md
    assert "original:" in card.body_md
    # Original snippet still in body for audit -- the change_request: block
    # is the executor's note that lives separately.
    assert "change_request:" in card.body_md
    # Marker JSON records the auto_edit decision.
    marker = amendment_review_marker_path(paths, "bAMA-01")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["auto_edit"]["applied"] is True
    assert payload["auto_edit"]["ac_index"] == 0
    assert payload["auto_edit"]["confidence"] == 0.99


def test_auto_edit_event_payload_records_provenance(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    editor = StaticAmendmentEditorClient(
        default=AmendmentEdit(
            ac_index=0, description="d", check_type="file_exists",
            check_fields={"path": "x.md"},
            amendment_reason="why", confidence=0.95, model_used="m",
        ),
    )
    run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=_approving_reviewer(),
        reviewer_config=ReviewerConfig(enabled=True, auto_edit_ac=True),
        editor_client=editor,
    )
    events = repo.list_events("bAMA-01")
    edited = [
        e for e in events
        if e.type == EventType.AMENDED.value
        and (e.payload or {}).get("outcome") == "approved_and_edited"
    ]
    assert edited, "expected an approved_and_edited event"
    assert edited[0].payload["ac_index"] == 0
    assert edited[0].payload["amendment_reason"] == "why"


# ---- auto_edit_ac=True: fallbacks ------------------------------------


def test_auto_edit_below_confidence_floor_falls_back(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    editor = StaticAmendmentEditorClient(
        default=AmendmentEdit(
            ac_index=0, description="ok", check_type="file_exists",
            check_fields={"path": "x.md"},
            amendment_reason="x", confidence=0.5, model_used="m",
        ),
    )
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=_approving_reviewer(),
        reviewer_config=ReviewerConfig(
            enabled=True, auto_edit_ac=True,
            auto_edit_confidence_floor=0.85,
        ),
        editor_client=editor,
    )
    assert outcomes[0].action == "reviewed_approve"
    assert "below floor" in outcomes[0].reason
    card = repo.get_card("bAMA-01")
    assert card.status == CardStatus.BLOCKED.value
    assert card.merge_status == "amendment_approved"
    marker = amendment_review_marker_path(paths, "bAMA-01")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["auto_edit"]["applied"] is False
    assert payload["auto_edit"]["confidence"] == 0.5


def test_auto_edit_returns_none_falls_back(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    editor = StaticAmendmentEditorClient(default=None)
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=_approving_reviewer(),
        reviewer_config=ReviewerConfig(enabled=True, auto_edit_ac=True),
        editor_client=editor,
    )
    assert outcomes[0].action == "reviewed_approve"
    card = repo.get_card("bAMA-01")
    assert card.status == CardStatus.BLOCKED.value


def test_auto_edit_invalid_index_falls_back(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    editor = StaticAmendmentEditorClient(
        default=AmendmentEdit(
            ac_index=99, description="d", check_type="file_exists",
            amendment_reason="x", confidence=0.95,
        ),
    )
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=_approving_reviewer(),
        reviewer_config=ReviewerConfig(enabled=True, auto_edit_ac=True),
        editor_client=editor,
    )
    assert outcomes[0].action == "reviewed_approve"
    assert "splice failed" in outcomes[0].reason
    card = repo.get_card("bAMA-01")
    assert card.status == CardStatus.BLOCKED.value


def test_auto_edit_deny_path_unchanged(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    """Editor is never consulted when the reviewer denies the change."""
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    denying = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="request_changes", reasoning="no", confidence=0.95,
        ),
    )
    editor = StaticAmendmentEditorClient(
        default=AmendmentEdit(
            ac_index=0, description="ignored", check_type="file_exists",
            amendment_reason="x", confidence=0.99,
        ),
    )
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=denying,
        reviewer_config=ReviewerConfig(enabled=True, auto_edit_ac=True),
        editor_client=editor,
    )
    assert outcomes[0].action == "reviewed_deny"
    card = repo.get_card("bAMA-01")
    assert card.status == CardStatus.ACTIVE.value
    assert editor.calls == []


def test_auto_edit_comment_path_unchanged(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    _insert_amend_card(repo)
    paths = RuntimePaths.from_root(tmp_path)
    paths.ensure()
    commenting = StaticSiblingReviewerClient(
        default=ReviewerDecision(
            decision="comment", reasoning="unclear", confidence=0.3,
        ),
    )
    editor = StaticAmendmentEditorClient(
        default=AmendmentEdit(
            ac_index=0, description="ignored", check_type="file_exists",
            amendment_reason="x", confidence=0.99,
        ),
    )
    outcomes = run_amendment_reviews(
        repo=repo, cfg=_cfg(tmp_path), paths=paths,
        reviewer_client=commenting,
        reviewer_config=ReviewerConfig(enabled=True, auto_edit_ac=True),
        editor_client=editor,
    )
    assert outcomes[0].action == "reviewed_comment"
    card = repo.get_card("bAMA-01")
    assert card.status == CardStatus.AMENDMENTS.value
    assert editor.calls == []


def test_project_config_loads_auto_edit_fields(tmp_path: Path) -> None:
    """End-to-end: project.yaml -> ProjectConfig -> ReviewerConfig."""
    from cards_runner.common.project_config import load_project_config

    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(
        """
        reviewers:
          amendment:
            enabled: true
            auto_edit_ac: true
            auto_edit_confidence_floor: 0.7
            model: claude-sonnet-4-6
            label: editing-reviewer
        """,
        encoding="utf-8",
    )
    pc = load_project_config(cfg_path)
    assert pc.amendment_reviewer.auto_edit_ac is True
    assert pc.amendment_reviewer.auto_edit_confidence_floor == 0.7
    assert pc.amendment_reviewer.label == "editing-reviewer"


def test_project_config_default_auto_edit_floor_is_high(tmp_path: Path) -> None:
    from cards_runner.common.project_config import load_project_config

    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(
        """
        reviewers:
          amendment:
            enabled: true
        """,
        encoding="utf-8",
    )
    pc = load_project_config(cfg_path)
    assert pc.amendment_reviewer.auto_edit_ac is False
    assert pc.amendment_reviewer.auto_edit_confidence_floor == 0.85
