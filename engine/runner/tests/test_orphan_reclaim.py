"""Orphan reclaim, store-backed.

We create a card, claim it through the store, rewind its
`last_heartbeat` past the orphan threshold, then assert the orphan
scan flags it and that `reclaim` returns it to `backlog` with the
claim fields cleared.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from cards_runner.common.types import DaemonConfig
from cards_runner.daemon.orphan import force_reclaim, reclaim, scan_for_orphans
from cards_runner.store import CardStatus
from cards_runner.store.sqlite_store import SqliteRepository


def _stale_iso(*, minutes: int) -> str:
    stale = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
    return stale.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_scan_finds_orphan_after_heartbeat_goes_stale(
    repo: SqliteRepository, card_factory: Any, daemon_cfg: DaemonConfig
) -> None:
    card_factory("bTST-04-orphan")
    repo.claim_card("bTST-04-orphan", claimed_by="tester")
    # Fresh claim: no orphans.
    assert scan_for_orphans(repo=repo, cfg=daemon_cfg) == []
    # Rewind the heartbeat past the orphan window.
    repo.update_card_fields(
        "bTST-04-orphan",
        {"last_heartbeat": _stale_iso(minutes=daemon_cfg.orphan_timeout_minutes + 5)},
    )
    orphans = scan_for_orphans(repo=repo, cfg=daemon_cfg)
    assert orphans == ["bTST-04-orphan"]


def test_reclaim_returns_card_to_backlog_and_clears_fields(
    repo: SqliteRepository, card_factory: Any
) -> None:
    card_factory("bTST-05-reclaim")
    repo.claim_card("bTST-05-reclaim", claimed_by="tester")

    updated = reclaim(repo, "bTST-05-reclaim")
    assert updated.status == CardStatus.BACKLOG.value
    assert updated.claimed_by is None
    assert updated.started_at is None
    assert updated.last_heartbeat is None
    assert updated.attempt_trace_id is None
    # The store agrees.
    stored = repo.get_card("bTST-05-reclaim")
    assert stored is not None
    assert stored.status == CardStatus.BACKLOG.value


def test_reclaim_appends_a_reclaimed_event(
    repo: SqliteRepository, card_factory: Any
) -> None:
    card_factory("bTST-05b-event")
    repo.claim_card("bTST-05b-event", claimed_by="tester")
    reclaim(repo, "bTST-05b-event")
    event_types = [e.type for e in repo.list_events("bTST-05b-event")]
    # drafted (create) -> claimed -> reclaimed, in order.
    assert event_types == ["drafted", "claimed", "reclaimed"]


def test_force_reclaim_works_by_card_id(
    repo: SqliteRepository, card_factory: Any
) -> None:
    card_factory("bTST-06-force")
    repo.claim_card("bTST-06-force", claimed_by="tester")
    updated = force_reclaim(repo, "bTST-06-force")
    assert updated.status == CardStatus.BACKLOG.value


def test_force_reclaim_rejects_a_non_active_card(
    repo: SqliteRepository, card_factory: Any
) -> None:
    card_factory("bTST-06b-backlog")  # never claimed; still backlog.
    try:
        force_reclaim(repo, "bTST-06b-backlog")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError reclaiming a backlog card")


def test_force_reclaim_missing_card_raises(repo: SqliteRepository) -> None:
    try:
        force_reclaim(repo, "bTST-does-not-exist")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError for an unknown card")
