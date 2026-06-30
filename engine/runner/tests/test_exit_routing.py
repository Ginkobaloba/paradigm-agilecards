"""Daemon exit-code routing -- `_post_worker_exit` (chunk 2b-ii item 6).

Drives `_post_worker_exit` directly against a SQLite store: no worker
subprocess, no SDK, no tokens. It checks the four routing outcomes
(clean / cost-cap halt / cascade-exhausted halt / plain error), the
`escalated` event emission, and the enriched `executed` payload.
"""
from __future__ import annotations

import json
from typing import Any

from cards_runner.common.card_io import parse_card_file, write_card_file
from cards_runner.common.types import (
    EXIT_CLEAN,
    EXIT_COST_CAP_HALT,
    EXIT_HALT_SIGNAL,
    EXIT_STUB_ERROR,
    PROJECTED_CARD_NAME,
    WORKER_RESULT_NAME,
    ClaimedCard,
    DaemonConfig,
    RuntimePaths,
)
from cards_runner.daemon.daemon import Daemon, _WorkerHandle
from cards_runner.store import CardStatus
from cards_runner.store.projection import project_card_file
from cards_runner.store.sqlite_store import SqliteRepository


def _setup_claim(
    repo: SqliteRepository,
    card_factory: Any,
    paths: RuntimePaths,
    card_id: str,
    *,
    cascade: list[dict[str, Any]] | None = None,
    sidecar: dict[str, Any] | None = None,
) -> ClaimedCard:
    """Claim a card, project it, optionally seed cascade + sidecar."""
    card_factory(card_id)
    attempt = "att-" + card_id
    claimed = repo.claim_card(card_id, claimed_by="tester", attempt_trace_id=attempt)
    assert claimed is not None
    run_dir = paths.runs / attempt
    run_dir.mkdir(parents=True, exist_ok=True)
    card_file = run_dir / PROJECTED_CARD_NAME
    record = repo.get_card(card_id)
    assert record is not None
    project_card_file(record, card_file, verbatim=False)
    if cascade is not None:
        snap = parse_card_file(card_file)
        snap.frontmatter["cascade_history"] = cascade
        write_card_file(card_file, snap)
    if sidecar is not None:
        (run_dir / WORKER_RESULT_NAME).write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
    return ClaimedCard(
        card_id=card_id,
        attempt_trace_id=attempt,
        trace_id=attempt,
        run_dir=run_dir,
        worktree_path=run_dir / "worktree",
        card_file=card_file,
    )


def _handle(claim: ClaimedCard) -> _WorkerHandle:
    return _WorkerHandle(claim=claim, process=object(), spawned_at=0.0)  # type: ignore[arg-type]


def _event_types(repo: SqliteRepository, card_id: str) -> list[str]:
    return [e.type for e in repo.list_events(card_id)]


def test_clean_exit_keeps_card_active(
    repo: SqliteRepository, card_factory: Any, daemon_cfg: DaemonConfig,
    paths: RuntimePaths,
) -> None:
    claim = _setup_claim(repo, card_factory, paths, "bTST-50-clean",
                         sidecar={"exit_code": 0, "halt_kind": None})
    daemon = Daemon(daemon_cfg, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-50-clean")
    assert card is not None
    assert card.status == CardStatus.ACTIVE.value
    types = _event_types(repo, "bTST-50-clean")
    assert "executed" in types
    assert "blocked" not in types


def test_cost_cap_halt_routes_to_blocked(
    repo: SqliteRepository, card_factory: Any, daemon_cfg: DaemonConfig,
    paths: RuntimePaths,
) -> None:
    claim = _setup_claim(repo, card_factory, paths, "bTST-51-cost",
                         sidecar={"exit_code": 11, "halt_kind": "cost_cap"})
    daemon = Daemon(daemon_cfg, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_COST_CAP_HALT)

    card = repo.get_card("bTST-51-cost")
    assert card is not None
    assert card.status == CardStatus.BLOCKED.value
    types = _event_types(repo, "bTST-51-cost")
    assert "executed" in types and "blocked" in types


def test_cascade_exhausted_routes_to_blocked(
    repo: SqliteRepository, card_factory: Any, daemon_cfg: DaemonConfig,
    paths: RuntimePaths,
) -> None:
    claim = _setup_claim(
        repo, card_factory, paths, "bTST-52-casc",
        sidecar={"exit_code": 12, "halt_kind": "cascade_exhausted"},
    )
    daemon = Daemon(daemon_cfg, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_HALT_SIGNAL)

    card = repo.get_card("bTST-52-casc")
    assert card is not None
    assert card.status == CardStatus.BLOCKED.value
    assert "blocked" in _event_types(repo, "bTST-52-casc")


def test_plain_error_keeps_card_active_for_orphan_reclaim(
    repo: SqliteRepository, card_factory: Any, daemon_cfg: DaemonConfig,
    paths: RuntimePaths,
) -> None:
    claim = _setup_claim(repo, card_factory, paths, "bTST-53-err")
    daemon = Daemon(daemon_cfg, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_STUB_ERROR)

    card = repo.get_card("bTST-53-err")
    assert card is not None
    # A plain error is NOT a halt: the card stays active and orphan
    # reclaim returns it to backlog, exactly as in chunk 1.
    assert card.status == CardStatus.ACTIVE.value
    assert "blocked" not in _event_types(repo, "bTST-53-err")


def test_escalated_events_emitted_only_for_this_attempt(
    repo: SqliteRepository, card_factory: Any, daemon_cfg: DaemonConfig,
    paths: RuntimePaths,
) -> None:
    attempt = "att-bTST-54-esc"
    cascade = [
        {"from_tier": 2, "to_tier": 3, "reason": "low conf",
         "confidence_at_escalation": 0.3, "at": "2026-05-20T00:00:01Z",
         "attempt_trace_id": attempt},
        {"from_tier": 3, "to_tier": 4, "reason": "still low",
         "confidence_at_escalation": 0.4, "at": "2026-05-20T00:00:02Z",
         "attempt_trace_id": attempt},
        # A prior attempt's escalation, retained on the append-only
        # history -- it must NOT be re-emitted by this attempt.
        {"from_tier": 1, "to_tier": 2, "reason": "old run",
         "confidence_at_escalation": 0.5, "at": "2026-05-19T00:00:00Z",
         "attempt_trace_id": "att-some-older-run"},
    ]
    claim = _setup_claim(repo, card_factory, paths, "bTST-54-esc",
                         cascade=cascade)
    daemon = Daemon(daemon_cfg, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    escalated = [e for e in repo.list_events("bTST-54-esc")
                 if e.type == "escalated"]
    assert len(escalated) == 2
    assert {e.payload["to_tier"] for e in escalated} == {3, 4}


def test_executed_event_payload_carries_sidecar_detail(
    repo: SqliteRepository, card_factory: Any, daemon_cfg: DaemonConfig,
    paths: RuntimePaths,
) -> None:
    sidecar = {
        "exit_code": 0,
        "halt_kind": None,
        "actual_cost_usd": 0.0123,
        "model_used": "claude-haiku-4-5-20251001",
        "escalations": 1,
        "actual_tokens": 1500,
    }
    claim = _setup_claim(repo, card_factory, paths, "bTST-55-pay",
                         sidecar=sidecar)
    daemon = Daemon(daemon_cfg, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    executed = next(e for e in repo.list_events("bTST-55-pay")
                    if e.type == "executed")
    assert executed.payload["actual_cost_usd"] == 0.0123
    assert executed.payload["model_used"] == "claude-haiku-4-5-20251001"
    assert executed.payload["escalations"] == 1
    assert executed.payload["exit_code"] == 0
