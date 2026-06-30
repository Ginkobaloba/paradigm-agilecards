"""Claim concurrency tests against the real stores.

The claim is the load-bearing primitive: v1's atomic file move is
gone, replaced by a transactional claim (SQLite) and a transactional
branch/merge claim (Dolt). The contract is identical for both, and it
is exactly the property the v1 atomic-rename sentinel existed to
worry about: under genuine concurrency, a card is claimed by exactly
one runner.

These tests race real threads, each with its own repository and its
own connection, which is what a fleet of runner daemons looks like.
The `claim_store` fixture lives in conftest.py.
"""
from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from cards_runner.store.projection import card_text_to_record
from cards_runner.store.repository import CardRepository
from .store_support import make_card_text

RepoFactory = Callable[[], CardRepository]
ClaimStore = tuple[CardRepository, RepoFactory]


def _seed_card(repo: CardRepository, card_id: str) -> None:
    repo.create_card(card_text_to_record(make_card_text(card_id)))


@pytest.mark.timeout(120)
def test_eight_runners_race_one_card_exactly_one_wins(
    claim_store: ClaimStore,
) -> None:
    seed, factory = claim_store
    _seed_card(seed, "b001-01-contended")

    n_runners = 8
    barrier = threading.Barrier(n_runners)
    lock = threading.Lock()
    winners: list[str] = []
    errors: list[BaseException] = []

    def runner(idx: int) -> None:
        repo = factory()
        try:
            barrier.wait()
            claimed = repo.claim_card("b001-01-contended", claimed_by=f"runner-{idx}")
            if claimed is not None:
                with lock:
                    winners.append(claimed.claimed_by or "")
        except BaseException as exc:  # noqa: BLE001 - surfaced in the assert.
            with lock:
                errors.append(exc)
        finally:
            repo.close()

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(n_runners)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"claim raised under contention: {errors}"
    assert len(winners) == 1, f"expected exactly one winner, got {winners}"
    final = seed.get_card("b001-01-contended")
    assert final is not None
    assert final.status == "active"
    assert final.claimed_by == winners[0]


@pytest.mark.timeout(180)
def test_many_runners_many_cards_no_double_claim(
    claim_store: ClaimStore,
) -> None:
    seed, factory = claim_store
    card_ids = [f"b001-{i:02d}-card" for i in range(1, 13)]
    for card_id in card_ids:
        _seed_card(seed, card_id)

    n_runners = 6
    barrier = threading.Barrier(n_runners)
    lock = threading.Lock()
    claims: list[str] = []
    errors: list[BaseException] = []

    def runner(idx: int) -> None:
        repo = factory()
        try:
            barrier.wait()
            for card_id in card_ids:
                claimed = repo.claim_card(card_id, claimed_by=f"runner-{idx}")
                if claimed is not None:
                    with lock:
                        claims.append(card_id)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            repo.close()

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(n_runners)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"claim raised under contention: {errors}"
    # Every card claimed exactly once: no double claims, none missed.
    assert sorted(claims) == sorted(card_ids)
    assert len(claims) == len(set(claims))
    for card_id in card_ids:
        card = seed.get_card(card_id)
        assert card is not None and card.status == "active"
