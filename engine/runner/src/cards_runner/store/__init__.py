"""Storage substrate for the agile-cards runner.

This package is the chunk 2a deliverable: a database-canonical card
store behind a single repository interface, per
`docs/design/storage_substrate_v2.md` (Model B).

What lives here:

- `models`      -- the database-shaped data model (CardRecord,
                   CardEvent, Batch, Dependency, enums).
- `repository`  -- the abstract `CardRepository` interface plus the
                   shared SQL base class both concrete stores extend.
- `schema`      -- canonical column lists, per-dialect DDL, row
                   mapping helpers.
- `projection`  -- card `.md` <-> `CardRecord` conversion. The
                   lossless-migration core and the per-run file
                   projector.
- `sqlite_store`-- the SQLite implementation (stdlib, zero-ops).
- `dolt_store`  -- the Dolt implementation (default; git-style
                   branch/merge claim).
- `migrate_v1`  -- one-shot migration of v1 filesystem cards into a
                   store, with projection-diff verification.

Chunk 2a is deliberately additive: it ships the storage layer as a
standalone, tested package. It does NOT rewire the daemon. The
canonical cutover (the daemon's claim path moving off the filesystem,
the atomic-rename sentinel and the in-place YAML rewriter being
deleted) lands in chunk 2b alongside the real executor.
"""
from __future__ import annotations

from pathlib import Path

from .models import (
    DEFAULT_TENANT,
    ActorType,
    Batch,
    CardEvent,
    CardRecord,
    CardStatus,
    Dependency,
    EventType,
)
from .repository import (
    CardNotFound,
    CardRepository,
    DuplicateCard,
    RepositoryError,
    SchemaError,
)


def default_store_spec(todo_root: str | Path) -> str:
    """The store spec a deployment uses when none is given explicitly.

    SQLite, a single `cards.db` file under the TODO root. Per
    `storage_substrate_v2.md` section 4.5 SQLite is the default and
    the only store a solo deployment ever touches; Dolt is the opt-in
    for the distributed multi-runner case. The chunk 2b cutover ships
    on SQLite because Dolt is not installed on the host yet.
    """
    return f"sqlite:{Path(todo_root) / 'cards.db'}"


def build_repository(spec: str) -> CardRepository:
    """Construct a repository from a `sqlite:PATH` or `dolt:DIR` spec.

    The returned repository is NOT schema-initialized; the caller
    runs `initialize_schema()` (idempotent). The concrete-store
    imports are deferred so a SQLite deployment never imports the
    Dolt driver (`pymysql`) and vice versa.
    """
    if spec.startswith("sqlite:"):
        from .sqlite_store import SqliteRepository

        return SqliteRepository(spec[len("sqlite:"):])
    if spec.startswith("dolt:"):
        from .dolt_store import DoltRepository

        return DoltRepository.embedded(spec[len("dolt:"):])
    raise ValueError(
        f"unknown store spec {spec!r}; use sqlite:PATH or dolt:DIR"
    )


__all__ = [
    "DEFAULT_TENANT",
    "ActorType",
    "Batch",
    "CardEvent",
    "CardRecord",
    "CardStatus",
    "Dependency",
    "EventType",
    "CardNotFound",
    "CardRepository",
    "DuplicateCard",
    "RepositoryError",
    "SchemaError",
    "build_repository",
    "default_store_spec",
]
