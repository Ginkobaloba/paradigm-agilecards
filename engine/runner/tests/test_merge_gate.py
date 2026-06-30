"""Tests for `cards_runner.daemon.merge_gate`.

Drives `MergeGate.apply` directly against a fake gh runner, plus a
daemon-level test that exercises `_verifier_apply_pass` end-to-end with
the gate enabled. Covers:

- `decide_gate` for every tier band and pin override.
- `pr_gate_enabled=False` short-circuit (chunk-3 behavior preserved).
- auto-merge happy path: push + create + merge -> done/merged.
- auto-merge with merge failure -> blocked/conflict.
- sibling-review path -> blocked/requires_review with PR URL.
- human-review path (tier 5-6 or pin) -> blocked/open with PR URL.
- push failure routes to blocked/blocked with stderr tail.
- pin_required on a tier-1 card forces human review.
"""
from __future__ import annotations

import textwrap
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from cards_runner.common.types import ClaimedCard, DaemonConfig, RuntimePaths
from cards_runner.daemon.daemon import Daemon
from cards_runner.daemon.merge_gate import MergeGate, decide_gate
from cards_runner.daemon.pr_lifecycle import GhCallResult, NullGhRunner
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository


# ---- fakes -----------------------------------------------------------


@dataclass
class FakeGhRunner:
    """Records every call; returns scripted results.

    Tests configure the per-method outputs and inspect the recorded
    calls afterward. Each method returns the next queued
    `GhCallResult`, defaulting to ok=True when the queue is empty.
    """

    push_results: list[GhCallResult] = field(default_factory=list)
    open_pr_results: list[GhCallResult] = field(default_factory=list)
    merge_pr_results: list[GhCallResult] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    available: bool = True

    def is_available(self) -> bool:
        return self.available

    def push(self, worktree: Path, branch: str, *, set_upstream: bool = True) -> GhCallResult:
        self.calls.append(
            ("push", {"worktree": worktree, "branch": branch, "set_upstream": set_upstream})
        )
        return self.push_results.pop(0) if self.push_results else GhCallResult(ok=True)

    def open_pr(
        self,
        worktree: Path,
        *,
        title: str,
        body: str,
        base: str,
        draft: bool = False,
    ) -> GhCallResult:
        self.calls.append(
            (
                "open_pr",
                {"worktree": worktree, "title": title, "body": body,
                 "base": base, "draft": draft},
            )
        )
        if self.open_pr_results:
            return self.open_pr_results.pop(0)
        return GhCallResult(ok=True, parsed={"pr_url": "https://github.com/x/y/pull/42"})

    def merge_pr(
        self,
        worktree: Path,
        *,
        identifier: str,
        strategy: str = "squash",
    ) -> GhCallResult:
        self.calls.append(
            ("merge_pr", {"worktree": worktree, "identifier": identifier, "strategy": strategy})
        )
        return self.merge_pr_results.pop(0) if self.merge_pr_results else GhCallResult(ok=True)


# ---- helpers ---------------------------------------------------------


def _card_text(card_id: str, *, points: int = 2, pin_required: bool = False) -> str:
    trace = str(uuid.uuid4())
    return textwrap.dedent(
        f"""\
        ---
        verifier_schema_version: "1.3"
        id: {card_id}
        title: Merge gate test card
        project: /tmp/test-project
        status: backlog
        points: {points}
        stakes: low
        difficulty: shallow
        thinking_depth: shallow
        model: claude-haiku-4-5-20251001
        extended_thinking: false
        model_floor: haiku
        pin_required: {'true' if pin_required else 'false'}
        requires_pre_approval: false
        cost_cap_usd: null
        estimated_tokens: 0
        actual_tokens: null
        estimated_duration_minutes: 0
        actual_duration_minutes: null
        trace_id: {trace}
        depends_on: []
        touches: []
        batch: bTST
        story_hash: deadbeef
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


def _insert(repo: SqliteRepository, card_id: str, **kwargs: Any) -> None:
    text = _card_text(card_id, **kwargs)
    record = card_text_to_record(text, card_id_fallback=card_id)
    repo.create_card(record)


def _claim(repo: SqliteRepository, paths: RuntimePaths, card_id: str) -> ClaimedCard:
    attempt = "att-" + card_id
    claimed = repo.claim_card(card_id, claimed_by="tester", attempt_trace_id=attempt)
    assert claimed is not None
    run_dir = paths.runs / attempt
    worktree = run_dir / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    return ClaimedCard(
        card_id=card_id,
        attempt_trace_id=attempt,
        trace_id=attempt,
        run_dir=run_dir,
        worktree_path=worktree,
        card_file=run_dir / "card.md",
    )


def _cfg_pr_on(todo_root: Path, store_spec: str) -> DaemonConfig:
    return DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        skip_worktree=True,
        verifier_enabled=True,
        verifier_cascade_disabled=True,
        pr_gate_enabled=True,
    )


# ---- decide_gate -----------------------------------------------------


@pytest.mark.parametrize("points,expected", [
    (1, "auto"), (2, "auto"),
    (3, "sibling_review"), (4, "sibling_review"),
    (5, "human_review"), (6, "human_review"),
])
def test_decide_gate_by_points(
    repo: SqliteRepository, points: int, expected: str
) -> None:
    _insert(repo, f"bTST-G-{points}", points=points)
    record = repo.get_card(f"bTST-G-{points}")
    assert record is not None
    assert decide_gate(record) == expected


def test_decide_gate_pin_required_overrides_tier(repo: SqliteRepository) -> None:
    # A tier-1 card with pin_required=true on the card must go to human
    # review per RUNNER_CONTRACT.md "pin_required: true overrides any
    # per-project relaxation".
    _insert(repo, "bTST-G-pin", points=1, pin_required=True)
    record = repo.get_card("bTST-G-pin")
    assert record is not None
    assert decide_gate(record) == "human_review"


# ---- MergeGate.apply -------------------------------------------------


def test_apply_short_circuits_when_pr_gate_disabled(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    cfg = DaemonConfig(
        todo_root=todo_root, store_spec=store_spec, skip_worktree=True,
        pr_gate_enabled=False,
    )
    _insert(repo, "bTST-MG-off", points=2)
    claim = _claim(repo, paths, "bTST-MG-off")
    record = repo.get_card("bTST-MG-off")
    assert record is not None

    gh = FakeGhRunner()
    gate = MergeGate(cfg=cfg, gh=gh)
    out = gate.apply(claim, record, verified_at="2026-05-20T00:00:00Z")

    assert out.skipped is True
    assert out.to_status == CardStatus.DONE.value
    assert out.merge_status == "merged"
    # No gh calls when the gate is off.
    assert gh.calls == []


def test_apply_auto_merge_happy_path(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    cfg = _cfg_pr_on(todo_root, store_spec)
    _insert(repo, "bTST-MG-auto", points=2)
    claim = _claim(repo, paths, "bTST-MG-auto")
    record = repo.get_card("bTST-MG-auto")
    assert record is not None

    gh = FakeGhRunner()
    gate = MergeGate(cfg=cfg, gh=gh)
    out = gate.apply(claim, record, verified_at="2026-05-20T01:00:00Z")

    assert out.decision == "auto"
    assert out.to_status == CardStatus.DONE.value
    assert out.merge_status == "merged"
    assert out.pr_url == "https://github.com/x/y/pull/42"
    assert [c[0] for c in gh.calls] == ["push", "open_pr", "merge_pr"]


def test_apply_auto_merge_conflict_routes_to_blocked(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    cfg = _cfg_pr_on(todo_root, store_spec)
    _insert(repo, "bTST-MG-conf", points=1)
    claim = _claim(repo, paths, "bTST-MG-conf")
    record = repo.get_card("bTST-MG-conf")
    assert record is not None

    gh = FakeGhRunner(
        merge_pr_results=[GhCallResult(
            ok=False, exit_code=1, stderr="merge conflict on file.py",
            reason="exit 1: merge conflict on file.py",
        )],
    )
    gate = MergeGate(cfg=cfg, gh=gh)
    out = gate.apply(claim, record, verified_at="2026-05-20T02:00:00Z")

    assert out.to_status == CardStatus.BLOCKED.value
    assert out.merge_status == "conflict"
    assert out.pr_url == "https://github.com/x/y/pull/42"


def test_apply_sibling_review_opens_pr_no_merge(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    cfg = _cfg_pr_on(todo_root, store_spec)
    _insert(repo, "bTST-MG-sib", points=3)
    claim = _claim(repo, paths, "bTST-MG-sib")
    record = repo.get_card("bTST-MG-sib")
    assert record is not None

    gh = FakeGhRunner()
    gate = MergeGate(cfg=cfg, gh=gh)
    out = gate.apply(claim, record, verified_at="2026-05-20T03:00:00Z")

    assert out.decision == "sibling_review"
    assert out.to_status == CardStatus.BLOCKED.value
    assert out.merge_status == "requires_review"
    # Sibling path opens PR but does NOT call merge_pr.
    call_names = [c[0] for c in gh.calls]
    assert "merge_pr" not in call_names
    assert "open_pr" in call_names


def test_apply_human_review_for_high_tier(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    cfg = _cfg_pr_on(todo_root, store_spec)
    _insert(repo, "bTST-MG-hum", points=5)
    claim = _claim(repo, paths, "bTST-MG-hum")
    record = repo.get_card("bTST-MG-hum")
    assert record is not None

    gh = FakeGhRunner()
    gate = MergeGate(cfg=cfg, gh=gh)
    out = gate.apply(claim, record, verified_at="2026-05-20T04:00:00Z")

    assert out.decision == "human_review"
    assert out.to_status == CardStatus.BLOCKED.value
    assert out.merge_status == "open"
    assert "merge_pr" not in [c[0] for c in gh.calls]


def test_apply_pin_required_on_low_tier_forces_human(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    cfg = _cfg_pr_on(todo_root, store_spec)
    _insert(repo, "bTST-MG-pin", points=1, pin_required=True)
    claim = _claim(repo, paths, "bTST-MG-pin")
    record = repo.get_card("bTST-MG-pin")
    assert record is not None

    gh = FakeGhRunner()
    gate = MergeGate(cfg=cfg, gh=gh)
    out = gate.apply(claim, record, verified_at="2026-05-20T05:00:00Z")

    assert out.decision == "human_review"
    assert out.merge_status == "open"
    # No auto-merge attempt despite the low tier.
    assert "merge_pr" not in [c[0] for c in gh.calls]


def test_apply_push_failure_routes_to_blocked(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    cfg = _cfg_pr_on(todo_root, store_spec)
    _insert(repo, "bTST-MG-push", points=2)
    claim = _claim(repo, paths, "bTST-MG-push")
    record = repo.get_card("bTST-MG-push")
    assert record is not None

    gh = FakeGhRunner(
        push_results=[GhCallResult(ok=False, exit_code=1, stderr="rejected", reason="exit 1: rejected")],
    )
    gate = MergeGate(cfg=cfg, gh=gh)
    out = gate.apply(claim, record, verified_at="2026-05-20T06:00:00Z")

    assert out.to_status == CardStatus.BLOCKED.value
    assert out.merge_status == "blocked"
    assert "push failed" in out.reason
    # Failure stopped the chain after push.
    assert [c[0] for c in gh.calls] == ["push"]


# ---- daemon integration ----------------------------------------------


def test_daemon_verifier_pass_with_gate_auto_merge(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    """End-to-end: verifier PASS + gate on + tier 2 -> done/merged."""
    from cards_runner.common.types import EXIT_CLEAN, WORKER_RESULT_NAME, PROJECTED_CARD_NAME
    from cards_runner.daemon.daemon import _WorkerHandle
    from cards_runner.store.projection import project_card_file

    cfg = _cfg_pr_on(todo_root, store_spec)
    _insert(repo, "bTST-MG-int", points=2)
    record = repo.get_card("bTST-MG-int")
    assert record is not None

    # Project + claim, write a worktree file that satisfies the AC.
    attempt = "att-int"
    repo.claim_card("bTST-MG-int", claimed_by="tester", attempt_trace_id=attempt)
    run_dir = paths.runs / attempt
    worktree = run_dir / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    card_file = run_dir / PROJECTED_CARD_NAME
    project_card_file(repo.get_card("bTST-MG-int"), card_file, verbatim=False)
    (worktree / "anything").write_text("present", encoding="utf-8")
    (run_dir / WORKER_RESULT_NAME).write_text('{"exit_code": 0}', encoding="utf-8")

    claim = ClaimedCard(
        card_id="bTST-MG-int", attempt_trace_id=attempt, trace_id=attempt,
        run_dir=run_dir, worktree_path=worktree, card_file=card_file,
    )

    gh = FakeGhRunner()
    daemon = Daemon(cfg, repo=repo, gh=gh)
    daemon._post_worker_exit(_WorkerHandle(claim=claim, process=object(), spawned_at=0.0), EXIT_CLEAN)  # type: ignore[arg-type]

    card = repo.get_card("bTST-MG-int")
    assert card is not None
    assert card.status == CardStatus.DONE.value
    assert card.merge_status == "merged"
    assert card.verified_at is not None
    # Push + open + merge all fired.
    assert [c[0] for c in gh.calls] == ["push", "open_pr", "merge_pr"]
    types = [e.type for e in repo.list_events("bTST-MG-int")]
    assert "verified" in types and "merged" in types


def test_daemon_verifier_pass_with_gate_sibling_review(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    """Verifier PASS + tier 4 -> blocked/requires_review, PR opened."""
    from cards_runner.common.types import EXIT_CLEAN, WORKER_RESULT_NAME, PROJECTED_CARD_NAME
    from cards_runner.daemon.daemon import _WorkerHandle
    from cards_runner.store.projection import project_card_file

    cfg = _cfg_pr_on(todo_root, store_spec)
    _insert(repo, "bTST-MG-sib2", points=4)
    record = repo.get_card("bTST-MG-sib2")
    assert record is not None

    attempt = "att-sib"
    repo.claim_card("bTST-MG-sib2", claimed_by="tester", attempt_trace_id=attempt)
    run_dir = paths.runs / attempt
    worktree = run_dir / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    card_file = run_dir / PROJECTED_CARD_NAME
    project_card_file(repo.get_card("bTST-MG-sib2"), card_file, verbatim=False)
    (worktree / "anything").write_text("present", encoding="utf-8")
    (run_dir / WORKER_RESULT_NAME).write_text('{"exit_code": 0}', encoding="utf-8")

    claim = ClaimedCard(
        card_id="bTST-MG-sib2", attempt_trace_id=attempt, trace_id=attempt,
        run_dir=run_dir, worktree_path=worktree, card_file=card_file,
    )

    gh = FakeGhRunner()
    daemon = Daemon(cfg, repo=repo, gh=gh)
    daemon._post_worker_exit(_WorkerHandle(claim=claim, process=object(), spawned_at=0.0), EXIT_CLEAN)  # type: ignore[arg-type]

    card = repo.get_card("bTST-MG-sib2")
    assert card is not None
    assert card.status == CardStatus.BLOCKED.value
    assert card.merge_status == "requires_review"
    # Claim provenance was cleared.
    assert card.claimed_by is None
    # PR opened but never merged.
    call_names = [c[0] for c in gh.calls]
    assert "open_pr" in call_names and "merge_pr" not in call_names


# ---- NullGhRunner ----------------------------------------------------


def test_null_runner_returns_disabled_reason() -> None:
    null = NullGhRunner()
    assert null.is_available() is False
    assert null.push(Path("/tmp"), "x").reason == "gh disabled"
    assert null.open_pr(Path("/tmp"), title="t", body="b", base="main").reason == "gh disabled"
    assert null.merge_pr(Path("/tmp"), identifier="42").reason == "gh disabled"
