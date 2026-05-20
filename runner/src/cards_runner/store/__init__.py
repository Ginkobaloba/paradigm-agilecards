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

from .models import (
    DEFAULT_TENANT,
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

__all__ = [
    "DEFAULT_TENANT",
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
]
