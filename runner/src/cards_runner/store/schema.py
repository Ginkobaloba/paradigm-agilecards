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
    "work_type",
    "frontmatter_extra",
    "frontmatter_raw",
    "body_md",
    "updated_at",
)

# Ledger chunk 1 (throughput-metrics) tables. Each is a
# `CREATE TABLE IF NOT EXISTS` so a fresh init runs the full set and a
# legacy DB picks them up the first time it opens through the chunk-1
# schema. The columns mirror the spec verbatim:
#
# - `card_metrics` is the per-card normalized metrics row written by
#   chunk 2 (the write surface). Joined one-to-one with `cards` on
#   (tenant_id, card_id).
# - `metric_estimates` is the derived percentile cache the read API
#   serves. Slicing key is (tenant_id, work_type, tier) -- tenant_id
#   is added to match the system-wide convention even though the spec
#   showed the pair without it.
CARD_METRICS_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "card_id",
    "work_type",
    "tier",
    "pin_required",
    "contract_authored_at",
    "started_at",
    "finished_at",
    "agent_wall_seconds",
    "agent_attempts",
    "executor_tokens_total",
    "executor_cost_usd",
    "verifier_tokens_total",
    "reviewer_tokens_total",
    "human_review_wall_seconds",
    "rework_cycles",
    "diff_lines_added",
    "diff_lines_removed",
    "merge_gate",
    "merged_at",
    "regression_card_ids",
    "contract_survived",
    "incomplete_metrics",
    "updated_at",
)

METRIC_ESTIMATES_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "work_type",
    "tier",
    "n_samples",
    "agent_wall_seconds_p50",
    "agent_wall_seconds_p75",
    "agent_wall_seconds_p90",
    "executor_tokens_p50",
    "executor_tokens_p90",
    "human_review_wall_seconds_p50",
    "human_review_wall_seconds_p90",
    "rework_rate_mean",
    "contract_survival_rate",
    "last_calibrated_at",
    "prior_weight",
)

# Every table the chunk-1 DDL creates. Doctor reports presence/absence
# of each so an operator can confirm migrations actually applied. Order
# matches the SQLite + MySQL DDL lists.
EXPECTED_TABLES: tuple[str, ...] = (
    "cards",
    "card_events",
    "batches",
    "dependencies",
    "counters",
    "card_metrics",
    "metric_estimates",
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


def post_migration_ddl(dialect: str) -> list[str]:
    """DDL statements that must run AFTER `_apply_added_columns`.

    Indexes on columns added via `ADDED_COLUMNS` cannot live in the
    initial DDL pass: on a legacy database whose CREATE TABLE no-ops,
    the column doesn't exist yet at index-creation time and the
    statement raises. The post-migration list runs after the ALTER pass
    has reconciled the schema.

    Every statement is idempotent (`CREATE INDEX IF NOT EXISTS` for
    SQLite; MySQL/Dolt does not support the idempotent form, so the
    repository swallows the duplicate-index error itself).
    """
    if dialect == DIALECT_SQLITE:
        return _SQLITE_POST_MIGRATION_DDL
    if dialect == DIALECT_MYSQL:
        return _MYSQL_POST_MIGRATION_DDL
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
    # ledger chunk 1: work_type stamps the planner's taxonomy on every
    # card row. NULL on legacy / pre-ledger cards; the estimator
    # excludes those rows from training data via `incomplete_metrics`.
    ("cards", "work_type", "TEXT", "VARCHAR(32)"),
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
        work_type        TEXT,
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
    # Ledger chunk 1: per-card normalized metrics row. Joined 1:1 with
    # `cards` on (tenant_id, card_id). All metric fields are nullable
    # because cards populate them across the lifecycle; the read API
    # treats NULL as "not yet measured".
    #
    # `regression_card_ids` is a JSON-encoded string (TEXT here; LONGTEXT
    # in MySQL) so the daemon can append/replace the list without a
    # native JSON column. Matches the same "store owns the bytes"
    # convention as `frontmatter_extra` and `card_events.payload`.
    """
    CREATE TABLE IF NOT EXISTS card_metrics (
        tenant_id                 TEXT NOT NULL,
        card_id                   TEXT NOT NULL,
        work_type                 TEXT,
        tier                      INTEGER,
        pin_required              INTEGER,
        contract_authored_at      TEXT,
        started_at                TEXT,
        finished_at               TEXT,
        agent_wall_seconds        REAL,
        agent_attempts            INTEGER,
        executor_tokens_total     INTEGER,
        executor_cost_usd         REAL,
        verifier_tokens_total     INTEGER,
        reviewer_tokens_total     INTEGER,
        human_review_wall_seconds REAL,
        rework_cycles             INTEGER,
        diff_lines_added          INTEGER,
        diff_lines_removed        INTEGER,
        merge_gate                TEXT,
        merged_at                 TEXT,
        regression_card_ids       TEXT NOT NULL DEFAULT '[]',
        contract_survived         INTEGER,
        incomplete_metrics        INTEGER NOT NULL DEFAULT 0,
        updated_at                TEXT,
        PRIMARY KEY (tenant_id, card_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_card_metrics_bucket "
    "ON card_metrics (tenant_id, work_type, tier)",
    # Ledger chunk 1: derived percentile cache. PK is the slicing key
    # the estimator queries; rebuildable from `card_metrics` so we
    # treat the cache as recomputable rather than authoritative.
    """
    CREATE TABLE IF NOT EXISTS metric_estimates (
        tenant_id                       TEXT NOT NULL,
        work_type                       TEXT NOT NULL,
        tier                            INTEGER NOT NULL,
        n_samples                       INTEGER NOT NULL DEFAULT 0,
        agent_wall_seconds_p50          REAL,
        agent_wall_seconds_p75          REAL,
        agent_wall_seconds_p90          REAL,
        executor_tokens_p50             INTEGER,
        executor_tokens_p90             INTEGER,
        human_review_wall_seconds_p50   REAL,
        human_review_wall_seconds_p90   REAL,
        rework_rate_mean                REAL,
        contract_survival_rate          REAL,
        last_calibrated_at              TEXT,
        prior_weight                    REAL,
        PRIMARY KEY (tenant_id, work_type, tier)
    )
    """,
]

# Statements that depend on a column added via `ADDED_COLUMNS`. The
# initial DDL pass runs CREATE TABLE IF NOT EXISTS, which no-ops on a
# legacy DB whose schema predates the column; the column is then added
# by `_apply_added_columns`; the index lives here so it lands after
# both. Idempotent.
_SQLITE_POST_MIGRATION_DDL: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_cards_work_type "
    "ON cards (tenant_id, work_type)",
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
        work_type        VARCHAR(32),
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
    """
    CREATE TABLE IF NOT EXISTS card_metrics (
        tenant_id                 VARCHAR(64)  NOT NULL,
        card_id                   VARCHAR(128) NOT NULL,
        work_type                 VARCHAR(32),
        tier                      INT,
        pin_required              TINYINT,
        contract_authored_at      VARCHAR(32),
        started_at                VARCHAR(32),
        finished_at               VARCHAR(32),
        agent_wall_seconds        DOUBLE,
        agent_attempts            INT,
        executor_tokens_total     BIGINT,
        executor_cost_usd         DOUBLE,
        verifier_tokens_total     BIGINT,
        reviewer_tokens_total     BIGINT,
        human_review_wall_seconds DOUBLE,
        rework_cycles             INT,
        diff_lines_added          INT,
        diff_lines_removed        INT,
        merge_gate                VARCHAR(32),
        merged_at                 VARCHAR(32),
        regression_card_ids       LONGTEXT NOT NULL,
        contract_survived         TINYINT,
        incomplete_metrics        TINYINT NOT NULL DEFAULT 0,
        updated_at                VARCHAR(32),
        PRIMARY KEY (tenant_id, card_id),
        INDEX idx_card_metrics_bucket (tenant_id, work_type, tier)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS metric_estimates (
        tenant_id                       VARCHAR(64) NOT NULL,
        work_type                       VARCHAR(32) NOT NULL,
        tier                            INT         NOT NULL,
        n_samples                       INT         NOT NULL DEFAULT 0,
        agent_wall_seconds_p50          DOUBLE,
        agent_wall_seconds_p75          DOUBLE,
        agent_wall_seconds_p90          DOUBLE,
        executor_tokens_p50             BIGINT,
        executor_tokens_p90             BIGINT,
        human_review_wall_seconds_p50   DOUBLE,
        human_review_wall_seconds_p90   DOUBLE,
        rework_rate_mean                DOUBLE,
        contract_survival_rate          DOUBLE,
        last_calibrated_at              VARCHAR(32),
        prior_weight                    DOUBLE,
        PRIMARY KEY (tenant_id, work_type, tier)
    )
    """,
]

# MySQL/Dolt has no `CREATE INDEX IF NOT EXISTS`. The repository wraps
# each statement and tolerates the duplicate-index error so re-running
# `initialize_schema()` stays idempotent.
_MYSQL_POST_MIGRATION_DDL: list[str] = [
    "CREATE INDEX idx_cards_work_type ON cards (tenant_id, work_type)",
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
        "work_type": record.work_type,
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
        work_type=_opt_str(row.get("work_type")),
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
    "CARD_METRICS_COLUMNS",
    "METRIC_ESTIMATES_COLUMNS",
    "EXPECTED_TABLES",
    "DIALECT_SQLITE",
    "DIALECT_MYSQL",
    "BATCH_COUNTER_NAME",
    "ddl_statements",
    "card_record_to_row",
    "row_to_card_record",
    "DEFAULT_TENANT",
]
