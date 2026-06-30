"""Daemon restart finds `active` cards in the store and reconciles.

We simulate a daemon crash by:

1. Claiming a card through the store (no worker spawned).
2. Constructing a fresh `Daemon` against the same store.
3. Running `_boot()` directly -- what the production boot sequence
   does after acquiring the singleton lock.

The new daemon must NOT reclaim a card whose heartbeat is still
fresh, and must reclaim one whose heartbeat aged past the orphan
window. The chunk 1 malformed-claim repair path is gone: the
transactional claim stamps every claim field atomically, so the
"killed between move and stamp" window cannot exist -- the third
test asserts exactly that.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from cards_runner.common.types import DaemonConfig
from cards_runner.daemon.daemon import Daemon
from cards_runner.store import CardStatus
from cards_runner.store.sqlite_store import SqliteRepository


def _stale_iso(*, minutes: int) -> str:
    stale = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
    return stale.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_boot_leaves_fresh_active_card_alone(
    repo: SqliteRepository, card_factory: Any, daemon_cfg: DaemonConfig
) -> None:
    card_factory("bTST-07-restart")
    repo.claim_card("bTST-07-restart", claimed_by="tester")
    # Fresh daemon sharing the test's store. _boot() alone exercises
    # the reconcile path without blocking on the poll loop.
    Daemon(daemon_cfg, repo=repo)._boot()
    card = repo.get_card("bTST-07-restart")
    assert card is not None
    assert card.status == CardStatus.ACTIVE.value


def test_boot_reclaims_stale_active_card(
    repo: SqliteRepository, card_factory: Any, daemon_cfg: DaemonConfig
) -> None:
    card_factory("bTST-08-stale")
    repo.claim_card("bTST-08-stale", claimed_by="tester")
    repo.update_card_fields(
        "bTST-08-stale",
        {"last_heartbeat": _stale_iso(minutes=daemon_cfg.orphan_timeout_minutes + 1)},
    )
    Daemon(daemon_cfg, repo=repo)._boot()
    card = repo.get_card("bTST-08-stale")
    assert card is not None
    assert card.status == CardStatus.BACKLOG.value
    assert card.claimed_by is None
    assert card.attempt_trace_id is None


def test_transactional_claim_stamps_all_fields_atomically(
    repo: SqliteRepository, card_factory: Any
) -> None:
    """The transactional claim has no half-stamped window.

    v1 moved the file then stamped the frontmatter, leaving a card
    that could be in `active/` with no `claimed_by`. The store claim
    is one `UPDATE`: every claim field lands together or not at all.
    """
    card_factory("bTST-09-atomic")
    claimed = repo.claim_card("bTST-09-atomic", claimed_by="tester")
    assert claimed is not None
    assert claimed.status == CardStatus.ACTIVE.value
    assert claimed.claimed_by == "tester"
    assert claimed.started_at is not None
    assert claimed.last_heartbeat is not None
    assert claimed.attempt_trace_id is not None
