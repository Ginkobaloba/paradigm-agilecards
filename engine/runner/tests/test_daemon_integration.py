"""End-to-end integration: a synthetic backlog runs through the store.

The daemon claims cards out of the SQLite store, projects each into a
per-run dir, spawns a stub worker, and lands the worker's result back
into the store. We use 3 cards to keep CI fast; the loop primitives
are identical at any N.

Chunk 2b-i's stub worker does NOT move a card to `done`: that
transition is the verifier (chunk 3). What this test verifies is:

- All 3 cards get claimed (status `backlog` -> `active` in the store).
- Each card is claimed exactly once (one `claimed` event each).
- Each card ends with completion notes in its stored body and an
  `executed` event.
- The daemon shuts down cleanly when stopped.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from cards_runner.common.types import DaemonConfig
from cards_runner.daemon.daemon import Daemon
from cards_runner.store import CardStatus
from cards_runner.store.sqlite_store import SqliteRepository


@pytest.mark.timeout(60)
def test_three_card_backlog_runs_through_the_store(
    repo: SqliteRepository,
    card_factory: Any,
    daemon_cfg: DaemonConfig,
) -> None:
    card_factory("bTST-10-a")
    card_factory("bTST-10-b")
    card_factory("bTST-10-c")

    cfg = DaemonConfig(
        todo_root=daemon_cfg.todo_root,
        store_spec=daemon_cfg.store_spec,
        poll_interval_sec=0.1,
        max_parallel=2,
        orphan_timeout_minutes=60,
        heartbeat_interval_sec=0.2,
        stub_sleep_sec=0.4,
        skip_worktree=True,
        # The chunk 2 baseline this test exercises predates the
        # verifier. Disable it here so a clean stub-worker exit still
        # leaves the card `active`, as that chunk's contract said.
        # Chunk 3 verifier integration has its own end-to-end test.
        verifier_enabled=False,
    )
    # The daemon opens its OWN store connection inside its thread
    # (SQLite connections are thread-bound); the test thread keeps the
    # `repo` fixture connection. WAL makes the two safe on one file.
    d = Daemon(cfg)
    t = threading.Thread(target=d.run, daemon=True)
    t.start()

    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        if not repo.query_cards(status=CardStatus.BACKLOG.value):
            break
        time.sleep(0.2)
    # Let the last workers finish and be reaped into the store.
    time.sleep(2.0)
    d.stop()
    t.join(timeout=10.0)
    assert not t.is_alive()

    assert repo.query_cards(status=CardStatus.BACKLOG.value) == []
    for card_id in ("bTST-10-a", "bTST-10-b", "bTST-10-c"):
        card = repo.get_card(card_id)
        assert card is not None, f"{card_id} missing from store"
        assert card.status == CardStatus.ACTIVE.value, (
            f"{card_id} status={card.status}"
        )
        assert "Stub executor" in card.body_md, (
            f"{card_id} body missing completion notes"
        )
        event_types = [e.type for e in repo.list_events(card_id)]
        assert event_types.count("claimed") == 1, (
            f"{card_id} claimed {event_types.count('claimed')} times"
        )
        assert "executed" in event_types, (
            f"{card_id} has no executed event: {event_types}"
        )
