"""Daemon verifier dispatch on rc=0 -- the chunk 3 plug-in point.

Drives `_post_worker_exit` directly against a SQLite store with the
verifier enabled. No worker subprocess, no SDK, no tokens. Exercises
the four post-exit routing outcomes the verifier owns:

- PASS                  -> card transitions to `done`.
- FAIL                  -> card returns to `backlog` with `verifier_notes`.
- needs_standup_review  -> card transitions to `awaiting_standup_review`.
- VerifierError x 3     -> card transitions to `blocked`.

Plus the verifier-skip happy path (high-confidence cascade-clean run
with no subjective items skips the verifier and writes the skip
reason on the card).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

import cards_runner.daemon.daemon as daemon_module
from cards_runner.common.types import (
    EXIT_CLEAN,
    PROJECTED_CARD_NAME,
    WORKER_RESULT_NAME,
    ClaimedCard,
    DaemonConfig,
    RuntimePaths,
)
from cards_runner.daemon.daemon import Daemon, _WorkerHandle
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record, project_card_file
from cards_runner.store.sqlite_store import SqliteRepository


# --- helpers ------------------------------------------------------


def _card_text_with_body(card_id: str, body: str, *, points: int = 2) -> str:
    trace = str(uuid.uuid4())
    fm = (
        "---\n"
        'verifier_schema_version: "1.3"\n'
        f"id: {card_id}\n"
        f"title: Test card {card_id}\n"
        "project: /tmp/test-project\n"
        "status: backlog\n"
        f"points: {points}\n"
        "stakes: low\n"
        "difficulty: shallow\n"
        "thinking_depth: shallow\n"
        "model: claude-haiku-4-5-20251001\n"
        "extended_thinking: false\n"
        "model_floor: haiku\n"
        "pin_required: false\n"
        "requires_pre_approval: false\n"
        "cost_cap_usd: null\n"
        "estimated_tokens: 0\n"
        "actual_tokens: null\n"
        "estimated_duration_minutes: 0\n"
        "actual_duration_minutes: null\n"
        f"trace_id: {trace}\n"
        'sizing_note: "test card"\n'
        "depends_on: []\n"
        "touches: []\n"
        "batch: bTST\n"
        "story_hash: deadbeef\n"
        "created: 2026-05-19\n"
        "started_at: null\n"
        "finished_at: null\n"
        "claimed_by: null\n"
        "model_used: null\n"
        "last_heartbeat: null\n"
        f"branch: card/{card_id}\n"
        "base_branch: main\n"
        "merge_status: pending\n"
        "verified_at: null\n"
        "verified_by: null\n"
        "verifier_skipped_reason: null\n"
        "cascade_history: []\n"
        "verifier_cascade_history: []\n"
        "standup_reason: null\n"
        "---\n\n"
    )
    return fm + body + "\n"


def _seed_card(repo: SqliteRepository, card_id: str, body_block: str) -> None:
    text = _card_text_with_body(card_id, body_block)
    record = card_text_to_record(text, card_id_fallback=card_id)
    repo.create_card(record)


def _claim_and_project(
    repo: SqliteRepository, paths: RuntimePaths, card_id: str
) -> ClaimedCard:
    attempt = "att-" + card_id
    claim = repo.claim_card(card_id, claimed_by="tester", attempt_trace_id=attempt)
    assert claim is not None
    run_dir = paths.runs / attempt
    worktree = run_dir / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    card_file = run_dir / PROJECTED_CARD_NAME
    record = repo.get_card(card_id)
    assert record is not None
    project_card_file(record, card_file, verbatim=False)
    return ClaimedCard(
        card_id=card_id,
        attempt_trace_id=attempt,
        trace_id=attempt,
        run_dir=run_dir,
        worktree_path=worktree,
        card_file=card_file,
    )


def _handle(claim: ClaimedCard) -> _WorkerHandle:
    return _WorkerHandle(claim=claim, process=object(), spawned_at=0.0)  # type: ignore[arg-type]


def _write_sidecar(run_dir: Path, payload: dict[str, Any]) -> None:
    (run_dir / WORKER_RESULT_NAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )


_AC_FILE_EXISTS = (
    "```yaml\n"
    "acceptance_criteria:\n"
    "  - description: 'README must exist'\n"
    "    type: file_exists\n"
    "    path: README.md\n"
    "```\n"
)

_AC_FILE_ABSENT_README = (
    "```yaml\n"
    "acceptance_criteria:\n"
    "  - description: 'README must NOT exist'\n"
    "    type: file_absent\n"
    "    path: README.md\n"
    "```\n"
)


_AC_SUBJECTIVE = (
    "```yaml\n"
    "acceptance_criteria:\n"
    "  - description: 'taste-test the change'\n"
    "    type: subjective\n"
    "```\n"
)


# --- tests --------------------------------------------------------


def test_verifier_pass_transitions_to_done(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    _seed_card(repo, "bTST-V0-pass", "## Acceptance criteria\n\n" + _AC_FILE_EXISTS)
    claim = _claim_and_project(repo, paths, "bTST-V0-pass")
    (claim.worktree_path / "README.md").write_text("hi", encoding="utf-8")
    _write_sidecar(claim.run_dir, {"exit_code": 0, "halt_kind": None})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-V0-pass")
    assert card is not None
    assert card.status == CardStatus.DONE.value
    assert card.verified_at is not None
    assert card.verified_by == "runner-verifier"
    types = [e.type for e in repo.list_events("bTST-V0-pass")]
    assert "executed" in types and "verified" in types


def test_verifier_fail_returns_card_to_backlog(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    # AC says README must exist; the worktree has no README -> FAIL.
    _seed_card(repo, "bTST-V1-fail", "## Acceptance criteria\n\n" + _AC_FILE_EXISTS)
    claim = _claim_and_project(repo, paths, "bTST-V1-fail")
    _write_sidecar(claim.run_dir, {"exit_code": 0, "halt_kind": None})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-V1-fail")
    assert card is not None
    assert card.status == CardStatus.BACKLOG.value
    assert "verifier_notes" in card.body_md
    # Claim provenance cleared so the next claim is clean.
    assert card.claimed_by is None
    assert card.attempt_trace_id is None


def test_verifier_standup_routes_to_awaiting_standup(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    # Subjective item, cascade disabled -> goes straight to standup.
    _seed_card(repo, "bTST-V2-stand", "## Acceptance criteria\n\n" + _AC_SUBJECTIVE)
    claim = _claim_and_project(repo, paths, "bTST-V2-stand")
    _write_sidecar(claim.run_dir, {"exit_code": 0, "halt_kind": None})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-V2-stand")
    assert card is not None
    assert card.status == CardStatus.AWAITING_STANDUP_REVIEW.value
    assert card.field_value("standup_reason")


def test_verifier_internal_crash_after_retries_routes_to_blocked(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verifier that crashes 3 times sends the card to `blocked`."""
    _seed_card(repo, "bTST-V3-crash", "## Acceptance criteria\n\n" + _AC_FILE_EXISTS)
    claim = _claim_and_project(repo, paths, "bTST-V3-crash")
    _write_sidecar(claim.run_dir, {"exit_code": 0, "halt_kind": None})

    crash_count = {"n": 0}

    def _crashing_verifier(*args: Any, **kwargs: Any) -> Any:
        crash_count["n"] += 1
        raise daemon_module.VerifierError("synthetic boom")

    monkeypatch.setattr(daemon_module, "verify_card", _crashing_verifier)

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-V3-crash")
    assert card is not None
    assert card.status == CardStatus.BLOCKED.value
    assert crash_count["n"] == 3  # 1 attempt + 2 retries.


def test_verifier_disabled_leaves_card_active(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg: DaemonConfig,
) -> None:
    """Verifier off (chunk 2 baseline) -> clean exit stays active."""
    _seed_card(repo, "bTST-V4-off", "## Acceptance criteria\n\n" + _AC_FILE_EXISTS)
    claim = _claim_and_project(repo, paths, "bTST-V4-off")
    _write_sidecar(claim.run_dir, {"exit_code": 0, "halt_kind": None})

    daemon = Daemon(daemon_cfg, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-V4-off")
    assert card is not None
    assert card.status == CardStatus.ACTIVE.value
    types = [e.type for e in repo.list_events("bTST-V4-off")]
    assert "verified" not in types


def test_verifier_skip_when_high_confidence_no_subjective(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    """RUNNER_CONTRACT.md "When the verifier MAY be skipped".

    All four conditions hold: no cascade history, no subjective items,
    high executor confidence in the sidecar. The card auto-passes.
    """
    _seed_card(repo, "bTST-V5-skip", "## Acceptance criteria\n\n" + _AC_FILE_EXISTS)
    claim = _claim_and_project(repo, paths, "bTST-V5-skip")
    _write_sidecar(claim.run_dir, {
        "exit_code": 0, "halt_kind": None,
        "executor_confidence": 0.99,
    })

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-V5-skip")
    assert card is not None
    assert card.status == CardStatus.DONE.value
    assert card.field_value("verifier_skipped_reason") == "high-confidence cascade-clean run"
    # verified_by should be null on a skip, per RUNNER_CONTRACT.md.
    assert card.verified_by is None


def test_verifier_skip_blocked_by_cascade_history(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    """A non-empty cascade_history disqualifies skip (always cold-read)."""
    _seed_card(
        repo, "bTST-V6-noskip",
        "## Acceptance criteria\n\n" + _AC_FILE_EXISTS,
    )
    claim = _claim_and_project(repo, paths, "bTST-V6-noskip")
    # Inject a cascade entry into the projected card and re-write it,
    # so the daemon picks it up via the worker-exit field merge.
    from cards_runner.common.card_io import parse_card_file, write_card_file
    snap = parse_card_file(claim.card_file)
    snap.frontmatter["cascade_history"] = [
        {"from_tier": 2, "to_tier": 3, "reason": "x",
         "confidence_at_escalation": 0.4, "at": "2026-05-20T00:00:00Z",
         "attempt_trace_id": claim.attempt_trace_id}
    ]
    write_card_file(claim.card_file, snap)
    (claim.worktree_path / "README.md").write_text("hi", encoding="utf-8")
    _write_sidecar(claim.run_dir, {
        "exit_code": 0, "halt_kind": None, "executor_confidence": 0.99,
    })

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-V6-noskip")
    assert card is not None
    # The verifier still ran (cold read mandated by cascade history)
    # and PASSED because README exists.
    assert card.status == CardStatus.DONE.value
    assert card.verified_by == "runner-verifier"
    assert card.field_value("verifier_skipped_reason") is None


def test_verifier_cascade_history_is_append_only(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    """Pre-existing verifier_cascade_history is preserved on a new pass."""
    _seed_card(repo, "bTST-V7-app", "## Acceptance criteria\n\n" + _AC_SUBJECTIVE)
    # Stamp the card with an existing cascade-history entry from a
    # prior verifier run.
    repo.update_card_fields(
        "bTST-V7-app",
        {
            "verifier_cascade_history": [
                {"tier_attempted": "haiku", "model": "old", "confidence": 0.5,
                 "result": "fail", "reasoning": "earlier run", "at": "2026-05-19T00:00:00Z",
                 "item_idx": 0}
            ]
        },
    )
    claim = _claim_and_project(repo, paths, "bTST-V7-app")
    _write_sidecar(claim.run_dir, {"exit_code": 0, "halt_kind": None})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-V7-app")
    history = card.field_value("verifier_cascade_history")
    assert isinstance(history, list)
    # At least the prior entry (1) plus a new disabled-cascade entry (1).
    assert len(history) >= 2
    assert history[0]["model"] == "old"  # prior entry preserved at the front.
