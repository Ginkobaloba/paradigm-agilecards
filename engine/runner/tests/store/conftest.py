"""Fixtures for the storage-layer test suite.

`repo` is parametrized over both concrete stores so a test written
once runs against SQLite and against Dolt. `claim_store` yields a
shared store plus a factory of fresh per-thread client repositories,
for the concurrency tests. Dolt is skipped (not failed) when the
`dolt` binary is not on the host.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from cards_runner.store.dolt_store import DoltRepository, DoltServer
from cards_runner.store.repository import CardRepository
from cards_runner.store.sqlite_store import SqliteRepository
from .store_support import dolt_available

RepoFactory = Callable[[], CardRepository]


@pytest.fixture(params=["sqlite", "dolt"])
def repo(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[CardRepository]:
    """A fresh, schema-initialized repository, once per store kind."""
    if request.param == "sqlite":
        store: CardRepository = SqliteRepository.open(str(tmp_path / "cards.db"))
    else:
        if not dolt_available():
            pytest.skip("dolt binary not available")
        store = DoltRepository.embedded(str(tmp_path / "doltstore"))
    try:
        yield store
    finally:
        store.close()


@pytest.fixture(params=["sqlite", "dolt"])
def claim_store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[CardRepository, RepoFactory]]:
    """Yield a seed repository and a factory of fresh same-store clients.

    The seed repository creates and inspects cards on the test
    thread; the factory hands each racing thread its own connection
    to the one shared store. Each worker is responsible for closing
    the repository it gets, since a SQLite connection is bound to its
    creating thread.
    """
    if request.param == "sqlite":
        db = str(tmp_path / "cards.db")
        seed: CardRepository = SqliteRepository.open(db)

        def factory() -> CardRepository:
            return SqliteRepository(db)

        try:
            yield seed, factory
        finally:
            seed.close()
    else:
        if not dolt_available():
            pytest.skip("dolt binary not available")
        server = DoltServer(str(tmp_path / "doltstore"))
        server.start()
        seed = DoltRepository.connect(server)
        seed.initialize_schema()

        def factory() -> CardRepository:
            return DoltRepository.connect(server)

        try:
            yield seed, factory
        finally:
            seed.close()
            server.stop()
