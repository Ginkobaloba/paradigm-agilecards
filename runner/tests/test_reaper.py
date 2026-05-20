"""Tests for `cards_runner.daemon.reaper`.

The reaper deletes per-attempt run dirs once `worktree_forensic_ttl_hours`
has expired, while leaving:

- dirs whose attempt_trace_id matches a still-running worker;
- dirs whose owning card is not in a reapable status;
- dirs newer than the TTL.

A removal failure is non-fatal and produces a `kept_unknown` decision
the daemon can log and retry on the next tick.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.reaper import reap_forensic_run_dirs
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository


def _seed_card(repo: SqliteRepository, card_id: str, *, status: str = "done",
               attempt_trace_id: str | None = None) -> None:
    import textwrap
    trace = str(uuid.uuid4())
    text = textwrap.dedent(
        f"""\
        ---
        verifier_schema_version: "1.3"
        id: {card_id}
        title: Reaper test
        project: /tmp/test-project
        status: {status}
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
    record = card_text_to_record(text, card_id_fallback=card_id, status_override=status)
    if attempt_trace_id:
        record.attempt_trace_id = attempt_trace_id
    repo.create_card(record)
    if attempt_trace_id:
        # create_card does not stamp attempt_trace_id; update_card_fields
        # writes the row with whatever the test wants.
        repo.update_card_fields(card_id, {"attempt_trace_id": attempt_trace_id})


def _make_run_dir(paths: RuntimePaths, attempt: str, *, age_seconds: float) -> Path:
    d = paths.runs / attempt
    d.mkdir(parents=True, exist_ok=True)
    (d / "marker.txt").write_text("present", encoding="utf-8")
    mtime = time.time() - age_seconds
    import os
    os.utime(d, (mtime, mtime))
    return d


def _cfg(todo_root: Path, *, ttl_hours: int = 24) -> DaemonConfig:
    return DaemonConfig(
        todo_root=todo_root, skip_worktree=True,
        worktree_forensic_ttl_hours=ttl_hours,
    )


def test_reaper_removes_old_dirs_with_no_owning_card(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    # No card claims this attempt_trace_id; the dir is genuinely orphan
    # forensic state. Reap once past the TTL.
    _make_run_dir(paths, "att-orphan", age_seconds=48 * 3600)
    decisions = reap_forensic_run_dirs(
        repo=repo, cfg=_cfg(todo_root, ttl_hours=24),
        paths=paths, in_flight_attempts=[],
    )
    assert any(d.action == "reaped" for d in decisions)
    assert not (paths.runs / "att-orphan").exists()


def test_reaper_keeps_dirs_under_ttl(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _make_run_dir(paths, "att-new", age_seconds=1 * 3600)
    decisions = reap_forensic_run_dirs(
        repo=repo, cfg=_cfg(todo_root, ttl_hours=24),
        paths=paths, in_flight_attempts=[],
    )
    actions = [d.action for d in decisions if d.path.name == "att-new"]
    assert actions == ["kept_recent"]
    assert (paths.runs / "att-new").exists()


def test_reaper_keeps_in_flight_dirs(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    # Even past the TTL, an in-flight attempt's dir must not be reaped.
    _make_run_dir(paths, "att-live", age_seconds=72 * 3600)
    decisions = reap_forensic_run_dirs(
        repo=repo, cfg=_cfg(todo_root, ttl_hours=24),
        paths=paths, in_flight_attempts=["att-live"],
    )
    actions = [d.action for d in decisions if d.path.name == "att-live"]
    assert actions == ["kept_active"]
    assert (paths.runs / "att-live").exists()


def test_reaper_keeps_dir_for_non_terminal_owning_card(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    # The card is still active and owns this attempt. The dir is past
    # the TTL but must be kept; the card is mid-flight in the store's
    # view (perhaps a worker is restarting around it).
    _seed_card(repo, "bTST-R0", status=CardStatus.ACTIVE.value, attempt_trace_id="att-r0")
    _make_run_dir(paths, "att-r0", age_seconds=72 * 3600)
    decisions = reap_forensic_run_dirs(
        repo=repo, cfg=_cfg(todo_root, ttl_hours=24),
        paths=paths, in_flight_attempts=[],
    )
    actions = [d.action for d in decisions if d.path.name == "att-r0"]
    assert actions == ["kept_active"]


def test_reaper_reaps_dirs_for_done_cards_past_ttl(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _seed_card(repo, "bTST-R1", status=CardStatus.DONE.value, attempt_trace_id="att-r1")
    _make_run_dir(paths, "att-r1", age_seconds=72 * 3600)
    decisions = reap_forensic_run_dirs(
        repo=repo, cfg=_cfg(todo_root, ttl_hours=24),
        paths=paths, in_flight_attempts=[],
    )
    actions = [d.action for d in decisions if d.path.name == "att-r1"]
    assert actions == ["reaped"]
    assert not (paths.runs / "att-r1").exists()


def test_reaper_disabled_when_ttl_is_zero(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    _make_run_dir(paths, "att-z", age_seconds=72 * 3600)
    decisions = reap_forensic_run_dirs(
        repo=repo, cfg=_cfg(todo_root, ttl_hours=0),
        paths=paths, in_flight_attempts=[],
    )
    assert decisions == []
    assert (paths.runs / "att-z").exists()


def test_reaper_skips_files_in_runs_root(
    repo: SqliteRepository, paths: RuntimePaths, todo_root: Path
) -> None:
    # A stray file in _runs/ must not be reaped or crash the walk.
    (paths.runs / "stray.log").write_text("noise", encoding="utf-8")
    decisions = reap_forensic_run_dirs(
        repo=repo, cfg=_cfg(todo_root),
        paths=paths, in_flight_attempts=[],
    )
    assert decisions == []
    assert (paths.runs / "stray.log").exists()
