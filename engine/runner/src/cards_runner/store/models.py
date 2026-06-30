"""The database-shaped card model.

`common.types.CardSnapshot` is the file-shaped view: a frontmatter
dict plus a body string, the thing the runner and the executor pass
around. `CardRecord` here is the database-shaped view: hot fields
promoted to typed attributes, the long tail in a JSON dict, and the
two verbatim capture fields (`frontmatter_raw`, `body_md`) that make
the round trip provably lossless.

`projection.py` converts between the two. Keeping the two models
apart is deliberate: the executor never sees a `CardRecord` and never
learns the database exists, exactly as `storage_substrate_v2.md`
section 3.2 requires.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Solo and single-tenant deployments use this one tenant and never
# see it. `tenant_id` is in the schema from day one because
# retrofitting a tenant key into a populated multi-table schema is
# among the most painful changes a project can defer
# (storage_substrate_v2.md section 6.3).
DEFAULT_TENANT: str = "default"


class CardStatus(str, Enum):
    """Canonical card states.

    These mirror the v1 subfolder names. The value is what lands in
    the `status` column and in the projected card's `status:` field.
    The one asymmetry v1 carried (the `amendments/` subfolder paired
    with the `awaiting_amendment_review` field value) is recorded in
    `FIELD_VALUE_OVERRIDES` so the projection stays contract-faithful.
    """

    BACKLOG = "backlog"
    ACTIVE = "active"
    AMENDMENTS = "amendments"
    AWAITING_STANDUP_REVIEW = "awaiting_standup_review"
    DONE = "done"
    BLOCKED = "blocked"


# RUNNER_CONTRACT.md: the `amendments/` subfolder pairs with the
# `awaiting_amendment_review` status field value. The store keeps the
# short form as the canonical status; the projector writes the long
# form into the card file's `status:` field for that one state.
FIELD_VALUE_OVERRIDES: dict[str, str] = {
    CardStatus.AMENDMENTS.value: "awaiting_amendment_review",
}


class EventType(str, Enum):
    """Card lifecycle event types.

    `card_events` is append-only and is populated from day one
    (storage_substrate_v2.md section 4.4). That keeps full event
    sourcing a refactor away rather than a rewrite away. This list is
    the chunk 2a vocabulary; chunk 2b adds executor-side detail
    (escalation probes, cost-cap halts) as it wires the real worker.
    """

    DRAFTED = "drafted"
    CLAIMED = "claimed"
    HEARTBEAT = "heartbeat"
    ESCALATED = "escalated"
    EXECUTED = "executed"
    VERIFIED = "verified"
    AMENDED = "amended"
    MERGED = "merged"
    BLOCKED = "blocked"
    RECLAIMED = "reclaimed"
    TRANSITIONED = "transitioned"
    MIGRATED = "migrated"


class ActorType(str, Enum):
    """Who or what caused an event. Carried on every `card_events` row.

    Per-actor audit ("everything agent X did", "every card a human
    approved") is the capability the filesystem substrate could not
    answer at all (storage_substrate_v2.md section 1.4).
    """

    HUMAN = "human"
    RUNNER = "runner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    PLANNER = "planner"
    MIGRATION = "migration"
    SYSTEM = "system"


@dataclass
class CardRecord:
    """One card, database-shaped.

    The promoted attributes are the hot, queried, indexable fields.
    `frontmatter_extra` holds every other frontmatter key so nothing
    is dropped. `frontmatter_raw` and `body_md` are verbatim captures:
    together they reproduce the source `.md` file byte-for-byte, which
    is how `migrate_v1` proves it lost nothing.

    The promoted fields are the queryable derivation of
    `frontmatter_raw`. After a write they are the live truth; the
    projector rebuilds a card file from them. `frontmatter_raw` itself
    is an immutable import-time capture and is never rewritten, so it
    keeps working as the migration-losslessness witness for the life
    of the row.
    """

    card_id: str
    tenant_id: str = DEFAULT_TENANT
    status: str = CardStatus.BACKLOG.value

    # Hot typed columns. All nullable: a card mid-lifecycle has many
    # of these unset, and the v1 templates ship them as `null`.
    title: str | None = None
    project: str | None = None
    batch: str | None = None
    points: int | None = None
    stakes: str | None = None
    difficulty: str | None = None
    claimed_by: str | None = None
    attempt_trace_id: str | None = None
    model_used: str | None = None
    created: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    last_heartbeat: str | None = None
    merge_status: str | None = None
    verified_at: str | None = None
    verified_by: str | None = None
    estimated_tokens: int | None = None
    actual_tokens: int | None = None
    story_hash: str | None = None
    trace_id: str | None = None
    # The merge gate's PR URL, promoted to a queryable column in chunk 5.
    # NULL until a verifier-pass card with `pr_gate_enabled=True` opens
    # a PR; the dashboard reads this so the operator can click straight
    # to the open PR without grepping the event log.
    pr_url: str | None = None
    # The card's work_type, promoted in ledger chunk 1 per
    # `docs/design/throughput_metrics_ledger.md` section 4. The planner
    # stamps one of the canonical values from
    # `common.types.CANONICAL_WORK_TYPES`; NULL on legacy / pre-ledger
    # cards, which the estimator excludes from its training set via
    # the `incomplete_metrics` flag (ledger chunk 2 wires that path).
    work_type: str | None = None

    # The long tail of the ~40-field frontmatter: every key not
    # promoted above. Stored as JSON. Keeps the schema stable against
    # a frontmatter that has changed every minor version.
    frontmatter_extra: dict[str, Any] = field(default_factory=dict)

    # Verbatim captures. The projection source of truth.
    frontmatter_raw: str = ""
    body_md: str = ""

    # Store bookkeeping. Set by the repository on write.
    updated_at: str | None = None

    def field_value(self, key: str) -> Any:
        """Read a frontmatter field by name, promoted column or tail.

        Lets callers treat a `CardRecord` like the frontmatter dict
        they would have parsed off disk, without caring which fields
        the schema chose to promote.
        """
        if key in _PROMOTED_FIELD_NAMES:
            return getattr(self, key)
        return self.frontmatter_extra.get(key)


# Frontmatter keys that map to a typed column on `cards`. `id` is the
# card_id; the rest are attributes of `CardRecord`. `projection.py`
# reads this set to split a parsed frontmatter dict into promoted
# columns versus the `frontmatter_extra` tail.
_PROMOTED_FIELD_NAMES: frozenset[str] = frozenset({
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
    "status",
})

# Promoted fields whose stored type is an integer rather than a
# string. Used by the projection layer to coerce on the way in.
INTEGER_FIELD_NAMES: frozenset[str] = frozenset({
    "points",
    "estimated_tokens",
    "actual_tokens",
})


@dataclass
class CardEvent:
    """One append-only row in `card_events`.

    `seq` is a per-card monotonic counter so a card's history has a
    total order independent of wall-clock timestamps. `payload` is
    free-form JSON: the claim's runner id, the escalation's tier
    delta, the verifier's verdict.
    """

    card_id: str
    type: str
    tenant_id: str = DEFAULT_TENANT
    seq: int = 0
    actor_id: str | None = None
    actor_type: str = ActorType.SYSTEM.value
    at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str | None = None


@dataclass
class Batch:
    """One `/cards` batch. Replaces the v1 `_batches/.counter` file.

    `batch_id` is the `b<NNN>` zero-padded id. The store owns the
    monotonic counter, which is what the file-plus-lock counter was
    badly approximating (storage_substrate_v2.md section 1.4).
    """

    batch_id: str
    tenant_id: str = DEFAULT_TENANT
    created: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass
class Dependency:
    """One `card_id -> depends_on_id` edge.

    Promoting `depends_on` to explicit edge rows makes the dependency
    graph queryable ("what is transitively blocked on card X"), which
    the filesystem substrate could not answer.
    """

    card_id: str
    depends_on_id: str
    tenant_id: str = DEFAULT_TENANT
