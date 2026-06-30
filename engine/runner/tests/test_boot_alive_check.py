"""Tests for `Daemon._boot_alive_check` (chunk 4).

Boot-time reclaim of `active` cards whose worker process is no longer
alive, without waiting for the orphan-timeout window. Drives `_boot()`
directly (no real worker spawned) and asserts the reclaim happened.
"""
from __future__ import annotations

import os
import textwrap
import uuid
from pathlib import Path

from cards_runner.common.types import DaemonConfig, RuntimePaths, now_utc_iso
from cards_runner.daemon.daemon import Daemon
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository


def _seed_active_card(
    repo: SqliteRepository, card_id: str, *, attempt_trace_id: str
) -> None:
    trace = str(uuid.uuid4())
    text = textwrap.dedent(
        f"""\
        ---
        verifier_schema_version: "1.3"
        id: {card_id}
        title: Boot alive check
        project: /tmp/test-project
        status: backlog
        points: 2
        stakes: low
        difficulty: shallow
        thinking_depth: shallow
        model: claude-haiku-4-5-20251001
        extended_thinking: false
        model_floor: haiku
        pin_required: false
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

        body.
        """
    )
    record = card_text_to_record(text, card_id_fallback=card_id)
    repo.create_card(record)
    # Move to active with a fresh heartbeat -- orphan scan would NOT
    # touch this card, so the alive-check is the only path that can
    # reclaim it.
    repo.transition(card_id, to_status=CardStatus.ACTIVE.value, fields={
        "claimed_by": "tester",
        "attempt_trace_id": attempt_trace_id,
        "started_at": now_utc_iso(),
        "last_heartbeat": now_utc_iso(),
    })


def _cfg(todo_root: Path, store_spec: str, *, alive_check: bool = True) -> DaemonConfig:
    return DaemonConfig(
        todo_root=todo_root, store_spec=store_spec,
        skip_worktree=True, verifier_enabled=False,
        boot_worker_alive_check=alive_check,
    )


def test_dead_worker_pid_reclaims_active_card(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    _seed_active_card(repo, "bTST-AB-dead", attempt_trace_id="att-dead")
    run_dir = paths.runs / "att-dead"
    run_dir.mkdir(parents=True, exist_ok=True)
    # A pid that is almost certainly not alive on a test host.
    (run_dir / "worker.pid").write_text("4294967295", encoding="utf-8")

    daemon = Daemon(_cfg(todo_root, store_spec), repo=repo)
    daemon._boot()

    card = repo.get_card("bTST-AB-dead")
    assert card is not None
    assert card.status == CardStatus.BACKLOG.value
    types = [e.type for e in repo.list_events("bTST-AB-dead")]
    assert "reclaimed" in types


def test_unparseable_pidfile_reclaims_card(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    _seed_active_card(repo, "bTST-AB-bad", attempt_trace_id="att-bad")
    run_dir = paths.runs / "att-bad"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "worker.pid").write_text("not-a-number", encoding="utf-8")

    daemon = Daemon(_cfg(todo_root, store_spec), repo=repo)
    daemon._boot()

    card = repo.get_card("bTST-AB-bad")
    assert card is not None
    assert card.status == CardStatus.BACKLOG.value


def test_live_pid_keeps_card_active(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    _seed_active_card(repo, "bTST-AB-live", attempt_trace_id="att-live")
    run_dir = paths.runs / "att-live"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Use this test process's pid; it is certainly alive.
    (run_dir / "worker.pid").write_text(str(os.getpid()), encoding="utf-8")

    daemon = Daemon(_cfg(todo_root, store_spec), repo=repo)
    daemon._boot()

    card = repo.get_card("bTST-AB-live")
    assert card is not None
    assert card.status == CardStatus.ACTIVE.value


def test_missing_pidfile_defers_to_heartbeat_path(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    # The alive check must not reclaim cards whose pidfile is missing
    # (a daemon racing the spawner can see this). The orphan-heartbeat
    # path remains the safety net.
    _seed_active_card(repo, "bTST-AB-none", attempt_trace_id="att-none")
    # No worker.pid file written.
    daemon = Daemon(_cfg(todo_root, store_spec), repo=repo)
    daemon._boot()

    card = repo.get_card("bTST-AB-none")
    assert card is not None
    assert card.status == CardStatus.ACTIVE.value


def test_alive_check_disabled_does_not_reclaim(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str
) -> None:
    # When the knob is off, even a dead pid does not reclaim.
    _seed_active_card(repo, "bTST-AB-off", attempt_trace_id="att-off")
    run_dir = paths.runs / "att-off"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "worker.pid").write_text("4294967295", encoding="utf-8")

    daemon = Daemon(_cfg(todo_root, store_spec, alive_check=False), repo=repo)
    daemon._boot()

    card = repo.get_card("bTST-AB-off")
    assert card is not None
    assert card.status == CardStatus.ACTIVE.value
