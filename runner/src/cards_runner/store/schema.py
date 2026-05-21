"""Canonical schema: column lists, per-dialect DDL, row mapping.

One logical schema, two SQL dialects. SQLite and Dolt (which speaks
the MySQL wire protocol) share almost all of their DML, so the
repository base class is dialect-generic; only the DDL and a handful
of type names differ, and those differences are isolated here.

Design choices worth stating:

- JSON-ish fields (`frontmatter_extra`, the batch manifest, event
  payloads) are stored as TEXT and serialized by the application,
  not as a native JSON column. A native JSON column normalizes key
  order and whitespace, which would quietly defeat the projection
  round-trip. The store owns the bytes.
- `tenant_id` is the first PK column on every table, present from the
  first migration, defaulting to `default` for solo deployments
  (storage_substrate_v2.md section 6.3).
- `cards` carries the verbatim `frontmatter_raw` and `body_md`
  capture columns. They are the witnesses the migration verifier
  diffs against the original files.
"""
from __future__ import annotations

import json
from typing import Any

from .models import DEFAULT_TENANT, CardRecord

# Ordered column list for the `cards` table. The repository builds
# INSERT and UPDATE statements off this list so the column set is
# defined in exactly one place.
CARD_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "card_id",
    "status",
    "title",
    "project",
    "batch",
    "points",
    "stakes",
    "difficulty",
    "claimed_by",
    "attempt_trace_id",
    "model_used",
    "created",
    "started_at",
    "finished_at",
    "last_heartbeat",
    "merge_status",
    "verified_at",
    "verified_by",
    "estimated_tokens",
    "actual_tokens",
    "story_hash",
    "trace_id",
    "pr_url",
    "frontmatter_extra",
    "frontmatter_raw",
    "body_md",
    "updated_at",
)

CARD_EVENT_COLUMNS: tuple[str, ...] = (
    "event_id",
    "tenant_id",
    "card_id",
    "seq",
    "type",
    "actor_id",
    "actor_type",
    "at",
    "payload",
)

BATCH_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "batch_id",
    "created",
    "manifest",
)

DEPENDENCY_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "card_id",
    "depends_on_id",
)

# Supported dialects.
DIALECT_SQLITE = "sqlite"
DIALECT_MYSQL = "mysql"  # Dolt speaks this.


def ddl_statements(dialect: str) -> list[str]:
    """Return the ordered list of CREATE TABLE statements for a dialect.

    Idempotent: every statement is `CREATE TABLE IF NOT EXISTS`.
    """
    if dialect == DIALECT_SQLITE:
        return _SQLITE_DDL
    if dialect == DIALECT_MYSQL:
        return _MYSQL_DDL
    raise ValueError(f"unknown dialect {dialect!r}")


# ---- column migrations ---------------------------------------------------
# Columns added after the initial CREATE TABLE. The repository runs these
# after every `initialize_schema()` to upgrade an existing database whose
# CREATE TABLE no-ops because the table already exists. The DDL itself
# carries the new column, so a freshly-created table never needs the
# migration; the migration is the upgrade path for existing rows.
#
# Each entry is `(table, column, sqlite_type, mysql_type)`. Add new
# columns by appending to this list. Removing or renaming a column needs
# a real migration tool -- that's out of scope here.
ADDED_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    # chunk 5: pr_url promoted from event payload to a queryable column.
    ("cards", "pr_url", "TEXT", "VARCHAR(512)"),
)


def added_column_alters(dialect: str) -> list[str]:
    """Return ALTER TABLE statements for columns the migration adds.

    These are NOT idempotent at the SQL level (both engines raise when
    re-adding an existing column). The repository wraps each call with a
    column-existence check, so calling this list against an up-to-date
    database is a no-op at runtime.
    """
    if dialect not in (DIALECT_SQLITE, DIALECT_MYSQL):
        raise ValueError(f"unknown dialect {dialect!r}")
    statements: list[str] = []
    for table, column, sqlite_type, mysql_type in ADDED_COLUMNS:
        col_type = sqlite_type if dialect == DIALECT_SQLITE else mysql_type
        statements.append(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    return statements


# --- SQLite DDL ------------------------------------------------------
# SQLite is dynamically typed; the type names are advisory. Composite
# primary keys and partial-free indexes are all supported.
_SQLITE_DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS cards (
        tenant_id        TEXT NOT NULL,
        card_id          TEXT NOT NULL,
        status           TEXT NOT NULL,
        title            TEXT,
        project          TEXT,
        batch            TEXT,
        points           INTEGER,
        stakes           TEXT,
        difficulty       TEXT,
        claimed_by       TEXT,
        attempt_trace_id TEXT,
        model_used       TEXT,
        created          TEXT,
        started_at       TEXT,
        finished_at      TEXT,
        last_heartbeat   TEXT,
        merge_status     TEXT,
        verified_at      TEXT,
        verified_by      TEXT,
        estimated_tokens INTEGER,
        actual_tokens    INTEGER,
        story_hash       TEXT,
        trace_id         TEXT,
        pr_url           TEXT,
        frontmatter_extra TEXT NOT NULL DEFAULT '{}',
        frontmatter_raw  TEXT NOT NULL DEFAULT '',
        body_md          TEXT NOT NULL DEFAULT '',
        updated_at       TEXT,
        PRIMARY KEY (tenant_id, card_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cards_status "
    "ON cards (tenant_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_cards_batch "
    "ON cards (tenant_id, batch)",
    """
    CREATE TABLE IF NOT EXISTS card_events (
        event_id   TEXT NOT NULL,
        tenant_id  TEXT NOT NULL,
        card_id    TEXT NOT NULL,
        seq        INTEGER NOT NULL,
        type       TEXT NOT NULL,
        actor_id   TEXT,
        actor_type TEXT NOT NULL,
        at         TEXT NOT NULL,
        payload    TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (event_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_card "
    "ON card_events (tenant_id, card_id, seq)",
    """
    CREATE TABLE IF NOT EXISTS batches (
        tenant_id TEXT NOT NULL,
        batch_id  TEXT NOT NULL,
        created   TEXT,
        manifest  TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (tenant_id, batch_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dependencies (
        tenant_id     TEXT NOT NULL,
        card_id       TEXT NOT NULL,
        depends_on_id TEXT NOT NULL,
        PRIMARY KEY (tenant_id, card_id, depends_on_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS counters (
        tenant_id TEXT NOT NULL,
        name      TEXT NOT NULL,
        value     INTEGER NOT NULL,
        PRIMARY KEY (tenant_id, name)
    )
    """,
]

# --- MySQL / Dolt DDL ------------------------------------------------
# Dolt enforces declared types. VARCHAR lengths are generous but
# bounded; the verbatim and JSON-ish columns are LONGTEXT.
_MYSQL_DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS cards (
        tenant_id        VARCHAR(64)  NOT NULL,
        card_id          VARCHAR(128) NOT NULL,
        status           VARCHAR(32)  NOT NULL,
        title            VARCHAR(512),
        project          VARCHAR(512),
        batch            VARCHAR(64),
        points           INT,
        stakes           VARCHAR(16),
        difficulty       VARCHAR(16),
        claimed_by       VARCHAR(128),
        attempt_trace_id VARCHAR(64),
        model_used       VARCHAR(64),
        created          VARCHAR(32),
        started_at       VARCHAR(32),
        finished_at      VARCHAR(32),
        last_heartbeat   VARCHAR(32),
        merge_status     VARCHAR(32),
        verified_at      VARCHAR(32),
        verified_by      VARCHAR(128),
        estimated_tokens BIGINT,
        actual_tokens    BIGINT,
        story_hash       VARCHAR(128),
        trace_id         VARCHAR(64),
        pr_url           VARCHAR(512),
        frontmatter_extra LONGTEXT NOT NULL,
        frontmatter_raw  LONGTEXT NOT NULL,
        body_md          LONGTEXT NOT NULL,
        updated_at       VARCHAR(32),
        PRIMARY KEY (tenant_id, card_id),
        INDEX idx_cards_status (tenant_id, status),
        INDEX idx_cards_batch (tenant_id, batch)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS card_events (
        event_id   VARCHAR(64) NOT NULL,
        tenant_id  VARCHAR(64) NOT NULL,
        card_id    VARCHAR(128) NOT NULL,
        seq        INT NOT NULL,
        type       VARCHAR(32) NOT NULL,
        actor_id   VARCHAR(128),
        actor_type VARCHAR(32) NOT NULL,
        at         VARCHAR(32) NOT NULL,
        payload    LONGTEXT NOT NULL,
        PRIMARY KEY (event_id),
        INDEX idx_events_card (tenant_id, card_id, seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batches (
        tenant_id VARCHAR(64) NOT NULL,
        batch_id  VARCHAR(64) NOT NULL,
        created   VARCHAR(32),
        manifest  LONGTEXT NOT NULL,
        PRIMARY KEY (tenant_id, batch_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dependencies (
        tenant_id     VARCHAR(64) NOT NULL,
        card_id       VARCHAR(128) NOT NULL,
        depends_on_id VARCHAR(128) NOT NULL,
        PRIMARY KEY (tenant_id, card_id, depends_on_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS counters (
        tenant_id VARCHAR(64) NOT NULL,
        name      VARCHAR(64) NOT NULL,
        value     BIGINT NOT NULL,
        PRIMARY KEY (tenant_id, name)
    )
    """,
]


def card_record_to_row(record: CardRecord) -> dict[str, Any]:
    """Flatten a `CardRecord` to a column-keyed dict for INSERT/UPDATE.

    `frontmatter_extra` is JSON-serialized with sorted keys so the
    same record always produces the same bytes (stable diffs, stable
    Dolt content hashes).
    """
    return {
        "tenant_id": record.tenant_id,
        "card_id": record.card_id,
        "status": record.status,
        "title": record.title,
        "project": record.project,
        "batch": record.batch,
        "points": record.points,
        "stakes": record.stakes,
        "difficulty": record.difficulty,
        "claimed_by": record.claimed_by,
        "attempt_trace_id": record.attempt_trace_id,
        "model_used": record.model_used,
        "created": record.created,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "last_heartbeat": record.last_heartbeat,
        "merge_status": record.merge_status,
        "verified_at": record.verified_at,
        "verified_by": record.verified_by,
        "estimated_tokens": record.estimated_tokens,
        "actual_tokens": record.actual_tokens,
        "story_hash": record.story_hash,
        "trace_id": record.trace_id,
        "pr_url": record.pr_url,
        "frontmatter_extra": json.dumps(record.frontmatter_extra, sort_keys=True),
        "frontmatter_raw": record.frontmatter_raw,
        "body_md": record.body_md,
        "updated_at": record.updated_at,
    }


def row_to_card_record(row: dict[str, Any]) -> CardRecord:
    """Rebuild a `CardRecord` from a column-keyed DB row dict."""
    extra_raw = row.get("frontmatter_extra") or "{}"
    extra = json.loads(extra_raw) if isinstance(extra_raw, str) else dict(extra_raw)
    return CardRecord(
        card_id=str(row["card_id"]),
        tenant_id=str(row["tenant_id"]),
        status=str(row["status"]),
        title=_opt_str(row.get("title")),
        project=_opt_str(row.get("project")),
        batch=_opt_str(row.get("batch")),
        points=_opt_int(row.get("points")),
        stakes=_opt_str(row.get("stakes")),
        difficulty=_opt_str(row.get("difficulty")),
        claimed_by=_opt_str(row.get("claimed_by")),
        attempt_trace_id=_opt_str(row.get("attempt_trace_id")),
        model_used=_opt_str(row.get("model_used")),
        created=_opt_str(row.get("created")),
        started_at=_opt_str(row.get("started_at")),
        finished_at=_opt_str(row.get("finished_at")),
        last_heartbeat=_opt_str(row.get("last_heartbeat")),
        merge_status=_opt_str(row.get("merge_status")),
        verified_at=_opt_str(row.get("verified_at")),
        verified_by=_opt_str(row.get("verified_by")),
        estimated_tokens=_opt_int(row.get("estimated_tokens")),
        actual_tokens=_opt_int(row.get("actual_tokens")),
        story_hash=_opt_str(row.get("story_hash")),
        trace_id=_opt_str(row.get("trace_id")),
        pr_url=_opt_str(row.get("pr_url")),
        frontmatter_extra=extra,
        frontmatter_raw=str(row.get("frontmatter_raw") or ""),
        body_md=str(row.get("body_md") or ""),
        updated_at=_opt_str(row.get("updated_at")),
    )


def _opt_str(value: Any) -> str | None:
    """Coerce a DB value to `str | None` without turning None into 'None'."""
    if value is None:
        return None
    return str(value)


def _opt_int(value: Any) -> int | None:
    """Coerce a DB value to `int | None`."""
    if value is None:
        return None
    return int(value)


# Counter name for the batch id sequence.
BATCH_COUNTER_NAME: str = "batch_seq"

__all__ = [
    "CARD_COLUMNS",
    "CARD_EVENT_COLUMNS",
    "BATCH_COLUMNS",
    "DEPENDENCY_COLUMNS",
    "DIALECT_SQLITE",
    "DIALECT_MYSQL",
    "BATCH_COUNTER_NAME",
    "ddl_statements",
    "card_record_to_row",
    "row_to_card_record",
    "DEFAULT_TENANT",
]
