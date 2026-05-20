"""Tests for `cards_runner.daemon.eligibility`.

The eligibility check is a pure function that reads the card store and
the filesystem and returns an `EligibilityResult`. These tests drive it
against a real SQLite store + a tmp signals dir, with no daemon thread.

Coverage:

- Dependency gating: zero deps, all met, partial-met, dep missing,
  dep done-but-unmerged.
- Story drift: source path absent, hash matches, hash drifts, source
  path unreadable.
- Pre-approval: not required, required + missing marker, required +
  present marker.
- Order of evaluation when several checks could fire.
"""
from __future__ import annotations

import hashlib
import textwrap
import uuid
from pathlib import Path

import pytest

from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.eligibility import EligibilityResult, evaluate_eligibility
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository


def _card_text(
    card_id: str,
    *,
    depends_on: list[str] | None = None,
    requires_pre_approval: bool = False,
    story_hash: str = "deadbeef",
    story_source_path: str | None = None,
    project: str = "/tmp/test-project",
    points: int = 2,
    status: str = "backlog",
) -> str:
    deps_yaml = "[" + ", ".join(depends_on or []) + "]"
    ssp_line = (
        f"story_source_path: {story_source_path}\n" if story_source_path else ""
    )
    trace = str(uuid.uuid4())
    body = textwrap.dedent(
        f"""\
        ---
        verifier_schema_version: "1.3"
        id: {card_id}
        title: Test card {card_id}
        project: {project}
        status: {status}
        points: {points}
        stakes: low
        difficulty: shallow
        thinking_depth: shallow
        model: claude-haiku-4-5-20251001
        extended_thinking: false
        model_floor: haiku
        pin_required: false
        requires_pre_approval: {'true' if requires_pre_approval else 'false'}
        cost_cap_usd: null
        estimated_tokens: 0
        actual_tokens: null
        estimated_duration_minutes: 0
        actual_duration_minutes: null
        trace_id: {trace}
        sizing_note: "test"
        depends_on: {deps_yaml}
        touches: []
        batch: bTST
        story_hash: {story_hash}
        created: 2026-05-19
        started_at: null
        finished_at: null
        claimed_by: null
        model_used: null
        last_heartbeat: null
        branch: card/{card_id}
        base_branch: main
        merge_status: pending
        verified_at: null
        verified_by: null
        verifier_skipped_reason: null
        cascade_history: []
        verifier_cascade_history: []
        standup_reason: null
        ---

        ## Acceptance criteria

        ```yaml
        acceptance_criteria:
          - description: trivial
            type: file_exists
            path: anything
        ```
        """
    )
    # Inject the optional story_source_path line after dedent so the
    # textwrap common-prefix calculation does not see a zero-indent
    # line and bail out.
    if ssp_line:
        body = body.replace(
            f"story_hash: {story_hash}\n",
            f"story_hash: {story_hash}\n{ssp_line}",
            1,
        )
    return body


def _insert(repo: SqliteRepository, card_id: str, **kwargs: object) -> None:
    text = _card_text(card_id, **kwargs)  # type: ignore[arg-type]
    record = card_text_to_record(text, card_id_fallback=card_id)
    repo.create_card(record)


def _cfg(todo_root: Path) -> DaemonConfig:
    return DaemonConfig(todo_root=todo_root, skip_worktree=True)


# ---- dependency gating ----------------------------------------------


def test_no_dependencies_is_claimable(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _insert(repo, "bTST-D0-leaf")
    record = repo.get_card("bTST-D0-leaf")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "claim"


def test_dependency_in_backlog_skips(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _insert(repo, "bTST-D1-dep")  # parent, still backlog.
    _insert(repo, "bTST-D1-child", depends_on=["bTST-D1-dep"])
    record = repo.get_card("bTST-D1-child")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "skip"
    assert out.kind == "dependency"
    unmet = out.detail and out.detail.get("unmet")
    assert isinstance(unmet, list) and unmet[0]["id"] == "bTST-D1-dep"


def test_dependency_done_but_unmerged_skips(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _insert(repo, "bTST-D2-dep")
    _insert(repo, "bTST-D2-child", depends_on=["bTST-D2-dep"])
    # Move the parent to done but leave merge_status pending.
    repo.transition(
        "bTST-D2-dep",
        to_status=CardStatus.DONE.value,
        fields={"merge_status": "pending"},
    )
    record = repo.get_card("bTST-D2-child")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "skip"
    assert out.kind == "dependency"


def test_dependency_done_and_merged_unblocks(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _insert(repo, "bTST-D3-dep")
    _insert(repo, "bTST-D3-child", depends_on=["bTST-D3-dep"])
    repo.transition(
        "bTST-D3-dep",
        to_status=CardStatus.DONE.value,
        fields={"merge_status": "merged"},
    )
    record = repo.get_card("bTST-D3-child")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "claim"


def test_missing_dependency_skips(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _insert(repo, "bTST-D4-child", depends_on=["bTST-D4-ghost"])
    record = repo.get_card("bTST-D4-child")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "skip"
    assert out.kind == "dependency"
    unmet = out.detail and out.detail.get("unmet")
    assert isinstance(unmet, list) and unmet[0]["id"] == "bTST-D4-ghost"
    assert "not found" in str(unmet[0]["reason"])


# ---- story drift ----------------------------------------------------


def test_story_hash_matches_passes(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, tmp_path: Path
) -> None:
    story_file = tmp_path / "story.md"
    story_file.write_text("the story text", encoding="utf-8")
    sha = hashlib.sha256(b"the story text").hexdigest()
    _insert(
        repo, "bTST-S0-ok",
        story_hash=sha, story_source_path=str(story_file),
    )
    record = repo.get_card("bTST-S0-ok")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "claim"


def test_story_hash_mismatch_blocks(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, tmp_path: Path
) -> None:
    story_file = tmp_path / "story.md"
    story_file.write_text("changed text", encoding="utf-8")
    _insert(
        repo, "bTST-S1-drift",
        story_hash="cafebabe", story_source_path=str(story_file),
    )
    record = repo.get_card("bTST-S1-drift")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "block"
    assert out.kind == "story_drift"
    detail = out.detail or {}
    assert detail["declared_hash"] == "cafebabe"
    # Actual hash is the sha256 of "changed text".
    assert detail["actual_hash"] == hashlib.sha256(b"changed text").hexdigest()


def test_story_path_absent_falls_back_to_skippable_state(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, tmp_path: Path
) -> None:
    # The card declares a path that does not exist on disk; the loader
    # treats it as a transient skip rather than a permanent block.
    missing = tmp_path / "nope.md"
    _insert(
        repo, "bTST-S2-unread",
        story_hash="cafebabe", story_source_path=str(missing),
    )
    record = repo.get_card("bTST-S2-unread")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "skip"
    assert out.kind == "story_drift"


def test_no_story_source_path_is_claimable(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    # The contract: "Without `story_source_path`, the runner skips this
    # check; `story_hash` is then just a forensic fingerprint".
    _insert(repo, "bTST-S3-no-source", story_hash="cafebabe")
    record = repo.get_card("bTST-S3-no-source")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "claim"


# ---- pre-approval ---------------------------------------------------


def test_pre_approval_not_required_is_claimable(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _insert(repo, "bTST-P0-ok", requires_pre_approval=False)
    record = repo.get_card("bTST-P0-ok")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "claim"


def test_pre_approval_required_without_marker_skips(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _insert(repo, "bTST-P1-wait", requires_pre_approval=True)
    record = repo.get_card("bTST-P1-wait")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "skip"
    assert out.kind == "pre_approval"


def test_pre_approval_marker_unlocks_claim(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    card_id = "bTST-P2-go"
    _insert(repo, card_id, requires_pre_approval=True)
    marker_dir = paths.signals / "preapproval"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / f"{card_id}.ok").write_text("ok", encoding="utf-8")
    record = repo.get_card(card_id)
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "claim"


# ---- order of evaluation --------------------------------------------


def test_pre_approval_check_precedes_dependency_check(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    # The card has an unmet dep AND missing approval. Pre-approval is
    # evaluated first; the dependency check never runs.
    _insert(repo, "bTST-O1-dep")
    _insert(repo, "bTST-O1-card",
            depends_on=["bTST-O1-dep"], requires_pre_approval=True)
    record = repo.get_card("bTST-O1-card")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.kind == "pre_approval"


def test_story_drift_check_precedes_dependency_check(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, tmp_path: Path
) -> None:
    story_file = tmp_path / "story.md"
    story_file.write_text("not what was hashed", encoding="utf-8")
    _insert(repo, "bTST-O2-dep")
    _insert(
        repo, "bTST-O2-card",
        depends_on=["bTST-O2-dep"],
        story_hash="cafebabe",
        story_source_path=str(story_file),
    )
    record = repo.get_card("bTST-O2-card")
    assert record is not None
    out = evaluate_eligibility(record, repo=repo, cfg=_cfg(todo_root), paths=paths)
    assert out.action == "block"
    assert out.kind == "story_drift"


def test_result_dataclass_is_frozen() -> None:
    out = EligibilityResult(action="claim", kind="ok")
    with pytest.raises(Exception):
        out.action = "block"  # type: ignore[misc]
