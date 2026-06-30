"""Daemon-level integration tests for chunk-5 plumbing.

The unit-level tests cover each module in isolation; these confirm that
the daemon wires the chunk-5 modules together correctly: the project
config reloads each tick, merge-gate relaxation flows through, and the
eligibility module honors the project-level story_source_path.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from cards_runner.common.project_config import (
    MergeGateRelaxation, ProjectConfig, ProjectConfigLoader,
)
from cards_runner.common.types import DaemonConfig
from cards_runner.daemon.daemon import Daemon
from cards_runner.daemon.eligibility import evaluate_eligibility
from cards_runner.daemon.merge_gate import decide_gate
from cards_runner.daemon.sibling_reviewer import StaticSiblingReviewerClient
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text


def test_decide_gate_relaxation_auto_merges_tier_3(
    repo: SqliteRepository
) -> None:
    record = card_text_to_record(_card_text("bI-01", points=3))
    relaxation = MergeGateRelaxation(auto_merge_tier_3_4=True)
    assert decide_gate(record, relaxation=relaxation) == "auto"


def test_decide_gate_pin_still_wins_over_relaxation(
    repo: SqliteRepository
) -> None:
    record = card_text_to_record(_card_text("bI-02", points=3, pin_required=True))
    relaxation = MergeGateRelaxation(auto_merge_tier_3_4=True)
    assert decide_gate(record, relaxation=relaxation) == "human_review"


def test_eligibility_uses_project_config_story_source(
    repo: SqliteRepository, todo_root: Path, tmp_path: Path
) -> None:
    """The eligibility check falls back to the project-config
    story_source_path when the card has none."""
    source_file = tmp_path / "story.md"
    source_file.write_text("hello world\n", encoding="utf-8")
    import hashlib
    correct_hash = hashlib.sha256(source_file.read_bytes()).hexdigest()
    bad_hash = "0" * 64

    text = _card_text("bI-03", points=2).replace(
        "story_hash: deadbeef",
        f"story_hash: {bad_hash}",
    )
    record = card_text_to_record(text)
    repo.create_card(record)
    pcfg = ProjectConfig(story_source_path=str(source_file))
    from cards_runner.common.types import RuntimePaths

    fetched = repo.get_card("bI-03")
    assert fetched is not None
    outcome = evaluate_eligibility(
        fetched, repo=repo,
        cfg=DaemonConfig(todo_root=todo_root, skip_worktree=True),
        paths=RuntimePaths.from_root(todo_root),
        project_config=pcfg,
    )
    # bad hash -> block
    assert outcome.action == "block"
    assert outcome.kind == "story_drift"

    # Fixing the card's story_hash makes the same card claimable.
    text2 = _card_text("bI-04", points=2).replace(
        "story_hash: deadbeef",
        f"story_hash: {correct_hash}",
    )
    record2 = card_text_to_record(text2)
    repo.create_card(record2)
    fetched2 = repo.get_card("bI-04")
    out2 = evaluate_eligibility(
        fetched2, repo=repo,
        cfg=DaemonConfig(todo_root=todo_root, skip_worktree=True),
        paths=RuntimePaths.from_root(todo_root),
        project_config=pcfg,
    )
    assert out2.action == "claim"


def test_daemon_reload_picks_up_new_project_yaml(
    repo: SqliteRepository, todo_root: Path, store_spec: str,
) -> None:
    cfg_path = todo_root / "project.yaml"
    cfg_path.write_text(
        "reviewers:\n  sibling:\n    enabled: false\n", encoding="utf-8"
    )
    cfg = DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        skip_worktree=True,
        verifier_enabled=False,
    )
    daemon = Daemon(cfg, repo=repo)
    daemon._boot()
    assert daemon.project_config.sibling_reviewer.enabled is False
    # Bump the file's mtime explicitly.
    time.sleep(0.05)
    cfg_path.write_text(
        "reviewers:\n  sibling:\n    enabled: true\n    label: agt\n",
        encoding="utf-8",
    )
    import os
    new_mtime = cfg_path.stat().st_mtime + 1
    os.utime(cfg_path, (new_mtime, new_mtime))
    daemon._tick()
    assert daemon.project_config.sibling_reviewer.enabled is True
    assert daemon.project_config.sibling_reviewer.label == "agt"


def test_daemon_constructor_accepts_injected_reviewers(
    repo: SqliteRepository, todo_root: Path, store_spec: str,
) -> None:
    cfg = DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        skip_worktree=True,
        verifier_enabled=False,
    )
    sibling = StaticSiblingReviewerClient()
    amend = StaticSiblingReviewerClient()
    daemon = Daemon(
        cfg, repo=repo,
        sibling_reviewer_client=sibling,
        amendment_reviewer_client=amend,
    )
    assert daemon._build_sibling_reviewer_client() is sibling
    assert daemon._build_amendment_reviewer_client() is amend


def test_daemon_tick_summary_includes_chunk5_keys(
    repo: SqliteRepository, todo_root: Path, store_spec: str,
) -> None:
    cfg = DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        skip_worktree=True,
        verifier_enabled=False,
        pr_unblock_enabled=False,
    )
    daemon = Daemon(cfg, repo=repo)
    daemon._boot()
    daemon._tick()
    summary = daemon.last_tick_summary
    for key in (
        "unblocked_to_done", "sibling_reviews", "amendment_reviews",
        "run_dirs_reaped",
    ):
        assert key in summary


def test_merge_gate_uses_project_pr_base_override(
    repo: SqliteRepository, todo_root: Path, store_spec: str,
    tmp_path: Path,
) -> None:
    """When project.yaml sets merge_gate.pr_base_branch, the merge
    gate's PR body / base flag should pick it up over the daemon
    default."""
    from cards_runner.common.types import ClaimedCard, RuntimePaths
    from tests.test_merge_gate import FakeGhRunner

    cfg = DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        skip_worktree=True,
        verifier_enabled=False,
        pr_gate_enabled=True,
    )
    fake_gh = FakeGhRunner()
    daemon = Daemon(cfg, repo=repo, gh=fake_gh,
                    project_config_loader=ProjectConfigLoader(None))
    daemon._project_loader._config = ProjectConfig(
        merge_gate=MergeGateRelaxation(pr_base_branch="develop")
    )
    daemon._boot()
    text = _card_text("bI-99", points=2).replace(
        "base_branch: main\n", "base_branch: null\n"
    )
    record = card_text_to_record(text)
    repo.create_card(record)
    claimed = repo.claim_card("bI-99", claimed_by="t")
    assert claimed is not None
    paths = RuntimePaths.from_root(todo_root)
    claim = ClaimedCard(
        card_id="bI-99",
        attempt_trace_id="att-1",
        trace_id=str(claimed.trace_id or "tr-1"),
        run_dir=paths.runs / "att-1",
        worktree_path=paths.runs / "att-1" / "worktree",
        card_file=paths.runs / "att-1" / "card.md",
    )
    daemon._verifier_apply_pass(claim, result=None, skip_reason="t")
    # The gate should have called open_pr with base="develop".
    open_calls = [c for c in fake_gh.calls if c[0] == "open_pr"]
    assert open_calls
    assert open_calls[0][1]["base"] == "develop"


def test_pytest_collection_marker() -> None:
    # Sanity check so empty test discovery is obvious.
    assert pytest is not None
