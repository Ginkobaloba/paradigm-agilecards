"""The repository interface and the shared SQL base class.

`CardRepository` is the single interface the rest of the runner is
meant to depend on. Swapping Dolt for SQLite (or, later, PostgreSQL)
is a constructor change and nothing else. This is the seam
`storage_substrate_v2.md` section 4.5 calls for: build the interface
now so the day a second engine is needed is a configuration change,
not a rewrite.

`_SqlCardRepository` is a shared base. SQLite and Dolt (over the
MySQL wire protocol) share almost all of their DML, so everything
except the connection plumbing and the claim primitive is
implemented once here. The claim is abstract on purpose: it is the
one operation whose correctness argument is genuinely
engine-specific (a guarded `UPDATE` for SQLite, a branch-then-merge
for Dolt), and the design pass treats it as the load-bearing
difference between the two stores.
"""
from __future__ import annotations

import abc
import json
import uuid
from typing import Any, Iterable

from ..common.types import now_utc_iso
from .models import (
    DEFAULT_TENANT,
    ActorType,
    Batch,
    CardEvent,
    CardRecord,
    CardStatus,
    EventType,
)
from .schema import (
    ADDED_COLUMNS,
    BATCH_COUNTER_NAME,
    CARD_COLUMNS,
    added_column_alters,
    card_record_to_row,
    ddl_statements,
    row_to_card_record,
)


class RepositoryError(Exception):
    """Base class for every storage-layer error."""


class SchemaError(RepositoryError):
    """Raised when schema creation or a schema assumption fails."""


class CardNotFound(RepositoryError):
    """Raised when an operation targets a card id that does not exist."""


class DuplicateCard(RepositoryError):
    """Raised when `create_card` is given a card id that already exists."""


class CardRepository(abc.ABC):
    """The card store interface.

    Every method takes a `tenant_id` (defaulting to the single
    `default` tenant a solo deployment never sees). Implementations
    must be safe to construct once per process and call from that
    process; cross-process and cross-thread safety is provided by the
    underlying engine, which is exactly the point of moving off the
    filesystem.
    """

    # ---- lifecycle ----------------------------------------------------

    @abc.abstractmethod
    def initialize_schema(self) -> None:
        """Create the schema if it does not exist. Idempotent."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release connections and any owned server process."""

    def __enter__(self) -> "CardRepository":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # ---- cards --------------------------------------------------------

    @abc.abstractmethod
    def create_card(self, record: CardRecord) -> CardRecord:
        """Insert a new card. Raises `DuplicateCard` on id collision.

        Also writes a `drafted` event and, from the card's
        `depends_on`, the matching `dependencies` edges.
        """

    @abc.abstractmethod
    def get_card(
        self, card_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> CardRecord | None:
        """Return one card, or None if it does not exist."""

    @abc.abstractmethod
    def query_cards(
        self,
        *,
        tenant_id: str = DEFAULT_TENANT,
        status: str | None = None,
        project: str | None = None,
        batch: str | None = None,
        claimed_by: str | None = None,
    ) -> list[CardRecord]:
        """Return cards matching the given filters, oldest `created` first."""

    @abc.abstractmethod
    def count_cards(self, *, tenant_id: str | None = None) -> int:
        """Return the card count, for a tenant or across all tenants."""

    @abc.abstractmethod
    def claim_card(
        self,
        card_id: str,
        *,
        claimed_by: str,
        attempt_trace_id: str | None = None,
        tenant_id: str = DEFAULT_TENANT,
    ) -> CardRecord | None:
        """Transactionally claim a backlog card.

        Returns the claimed `CardRecord` on success. Returns None if
        the card was not claimable: already claimed, not in backlog,
        or lost to a concurrent claimer. A None return is the
        caller's signal to move on to the next eligible card, exactly
        as a lost atomic-move race was in v1.

        On success the card is stamped `status=active`, `claimed_by`,
        `started_at`, `last_heartbeat`, `attempt_trace_id`, and a
        `claimed` event is appended.
        """

    @abc.abstractmethod
    def update_card_fields(
        self,
        card_id: str,
        fields: dict[str, Any],
        *,
        tenant_id: str = DEFAULT_TENANT,
    ) -> CardRecord:
        """Update card fields by frontmatter name. Raises `CardNotFound`.

        Promoted fields land in their typed column; everything else
        lands in `frontmatter_extra`. The verbatim `frontmatter_raw`
        capture is never touched: it is the immutable migration
        witness. No event is written; `transition` is the
        event-writing wrapper.
        """

    @abc.abstractmethod
    def transition(
        self,
        card_id: str,
        *,
        to_status: str,
        tenant_id: str = DEFAULT_TENANT,
        fields: dict[str, Any] | None = None,
        actor_id: str | None = None,
        actor_type: str = ActorType.RUNNER.value,
        event_type: str = EventType.TRANSITIONED.value,
        payload: dict[str, Any] | None = None,
    ) -> CardRecord:
        """Move a card to a new status and append a lifecycle event."""

    @abc.abstractmethod
    def apply_executor_result(
        self,
        card_id: str,
        *,
        tenant_id: str = DEFAULT_TENANT,
        body_md: str | None = None,
        fields: dict[str, Any] | None = None,
        event: CardEvent | None = None,
    ) -> CardRecord:
        """Write an executor's results back into the store.

        This is the chunk 2b worker-exit path: the runner has parsed
        the per-run projected card file and now lands the deltas the
        executor is allowed to produce (the body with its completion
        notes, plus the small set of executor-owned frontmatter
        fields) into the database, optionally with one lifecycle
        event. Unlike `transition` it does not change `status`; a
        stub or real executor finishing is not by itself a state
        transition (the verifier owns that, chunk 3).

        The verbatim `frontmatter_raw` capture is never touched.
        Raises `CardNotFound` if the card id does not exist.
        """

    # ---- events -------------------------------------------------------

    @abc.abstractmethod
    def append_event(self, event: CardEvent) -> CardEvent:
        """Append one append-only `card_events` row.

        The `seq` is assigned by the store as the next per-card
        counter value; any `seq` on the passed event is ignored.
        """

    @abc.abstractmethod
    def list_events(
        self, card_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> list[CardEvent]:
        """Return a card's events in `seq` order."""

    # ---- batches ------------------------------------------------------

    @abc.abstractmethod
    def create_batch(self, batch: Batch) -> Batch:
        """Insert a batch row."""

    @abc.abstractmethod
    def get_batch(
        self, batch_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> Batch | None:
        """Return one batch, or None."""

    @abc.abstractmethod
    def next_batch_id(self, *, tenant_id: str = DEFAULT_TENANT) -> str:
        """Atomically allocate the next `b<NNN>` batch id.

        Replaces the v1 `_batches/.counter` file-plus-lock with a real
        monotonic counter.
        """

    # ---- dependencies -------------------------------------------------

    @abc.abstractmethod
    def add_dependency(
        self,
        card_id: str,
        depends_on_id: str,
        *,
        tenant_id: str = DEFAULT_TENANT,
    ) -> None:
        """Record a `card_id -> depends_on_id` edge. Idempotent."""

    @abc.abstractmethod
    def get_dependencies(
        self, card_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> list[str]:
        """Return the card ids `card_id` depends on."""

    @abc.abstractmethod
    def get_dependents(
        self, card_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> list[str]:
        """Return the card ids that depend on `card_id`."""


class _SqlCardRepository(CardRepository):
    """Shared SQL implementation for the SQLite and Dolt stores.

    Subclasses provide four things: the dialect name, the placeholder
    token, a query/write pair against their connection, and a durable
    commit. `claim_card` stays abstract because its correctness
    argument differs per engine.
    """

    # Subclass-provided.
    _dialect: str = ""
    _ph: str = "?"  # parameter placeholder: "?" for sqlite, "%s" for mysql.

    # ---- subclass hooks ----------------------------------------------

    @abc.abstractmethod
    def _run_query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Run a SELECT, return rows as column-keyed dicts."""

    @abc.abstractmethod
    def _run_write(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Run an INSERT/UPDATE/DELETE/DDL, return the affected row count.

        Must not durably commit on its own; the base calls
        `_durable_commit` once per logical operation.
        """

    @abc.abstractmethod
    def _durable_commit(self, message: str) -> None:
        """Make pending writes durable.

        SQLite commits the transaction. Dolt makes a Dolt commit, so
        the change is visible to branches created afterward.
        """

    # ---- schema -------------------------------------------------------

    def initialize_schema(self) -> None:
        for statement in ddl_statements(self._dialect):
            self._run_write(statement)
        self._apply_added_columns()
        self._durable_commit("initialize schema")

    def _apply_added_columns(self) -> None:
        """Apply ALTER TABLE ADD COLUMN steps that the initial DDL omits.

        For a fresh database the CREATE TABLE already carries every
        post-chunk-4 column, so the per-column existence check returns
        True and the ALTER never runs. For an existing database whose
        CREATE TABLE no-ops (`IF NOT EXISTS`), this loop is the upgrade
        path -- it lets the runner pick up a new promoted column without
        forcing the operator to rebuild the store.
        """
        statements = added_column_alters(self._dialect)
        for (table, column, _stype, _mtype), statement in zip(
            ADDED_COLUMNS, statements
        ):
            if self._column_exists(table, column):
                continue
            self._run_write(statement)

    @abc.abstractmethod
    def _column_exists(self, table: str, column: str) -> bool:
        """True if `column` is present on `table` in the live database.

        Implemented per-engine (`PRAGMA table_info` for SQLite,
        `SHOW COLUMNS` for MySQL/Dolt) so the migration helper does not
        need to special-case the dialect string at the call site.
        """

    # ---- cards --------------------------------------------------------

    def create_card(self, record: CardRecord) -> CardRecord:
        if self.get_card(record.card_id, tenant_id=record.tenant_id) is not None:
            raise DuplicateCard(
                f"card {record.card_id!r} already exists for tenant "
                f"{record.tenant_id!r}"
            )
        record.updated_at = now_utc_iso()
        self._insert_card_row(record)
        self._insert_event_row(
            CardEvent(
                card_id=record.card_id,
                tenant_id=record.tenant_id,
                type=EventType.DRAFTED.value,
                actor_type=ActorType.PLANNER.value,
                at=record.created or now_utc_iso(),
                payload={"status": record.status},
            )
        )
        for dep in _as_str_list(record.field_value("depends_on")):
            self._insert_dependency_row(record.tenant_id, record.card_id, dep)
        self._durable_commit(f"create card {record.card_id}")
        got = self.get_card(record.card_id, tenant_id=record.tenant_id)
        assert got is not None  # just inserted.
        return got

    def get_card(
        self, card_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> CardRecord | None:
        rows = self._run_query(
            f"SELECT * FROM cards WHERE tenant_id = {self._ph} "
            f"AND card_id = {self._ph}",
            (tenant_id, card_id),
        )
        if not rows:
            return None
        return row_to_card_record(rows[0])

    def query_cards(
        self,
        *,
        tenant_id: str = DEFAULT_TENANT,
        status: str | None = None,
        project: str | None = None,
        batch: str | None = None,
        claimed_by: str | None = None,
    ) -> list[CardRecord]:
        clauses = [f"tenant_id = {self._ph}"]
        params: list[Any] = [tenant_id]
        for column, value in (
            ("status", status),
            ("project", project),
            ("batch", batch),
            ("claimed_by", claimed_by),
        ):
            if value is not None:
                clauses.append(f"{column} = {self._ph}")
                params.append(value)
        where = " AND ".join(clauses)
        # `created` ordered, card_id as the stable tie-break. This is
        # the FIFO ordering the v1 mtime sort approximated.
        rows = self._run_query(
            f"SELECT * FROM cards WHERE {where} ORDER BY created, card_id",
            tuple(params),
        )
        return [row_to_card_record(r) for r in rows]

    def count_cards(self, *, tenant_id: str | None = None) -> int:
        if tenant_id is None:
            rows = self._run_query("SELECT COUNT(*) AS n FROM cards")
        else:
            rows = self._run_query(
                f"SELECT COUNT(*) AS n FROM cards WHERE tenant_id = {self._ph}",
                (tenant_id,),
            )
        return int(rows[0]["n"])

    def update_card_fields(
        self,
        card_id: str,
        fields: dict[str, Any],
        *,
        tenant_id: str = DEFAULT_TENANT,
    ) -> CardRecord:
        record = self.get_card(card_id, tenant_id=tenant_id)
        if record is None:
            raise CardNotFound(f"card {card_id!r} (tenant {tenant_id!r})")
        _apply_fields(record, fields)
        record.updated_at = now_utc_iso()
        self._update_card_row(record)
        self._durable_commit(f"update card {card_id}")
        got = self.get_card(card_id, tenant_id=tenant_id)
        assert got is not None
        return got

    def transition(
        self,
        card_id: str,
        *,
        to_status: str,
        tenant_id: str = DEFAULT_TENANT,
        fields: dict[str, Any] | None = None,
        actor_id: str | None = None,
        actor_type: str = ActorType.RUNNER.value,
        event_type: str = EventType.TRANSITIONED.value,
        payload: dict[str, Any] | None = None,
    ) -> CardRecord:
        record = self.get_card(card_id, tenant_id=tenant_id)
        if record is None:
            raise CardNotFound(f"card {card_id!r} (tenant {tenant_id!r})")
        from_status = record.status
        merged: dict[str, Any] = {"status": to_status}
        if fields:
            merged.update(fields)
        _apply_fields(record, merged)
        record.updated_at = now_utc_iso()
        self._update_card_row(record)
        event_payload = {"from": from_status, "to": to_status}
        if payload:
            event_payload.update(payload)
        self._insert_event_row(
            CardEvent(
                card_id=card_id,
                tenant_id=tenant_id,
                type=event_type,
                actor_id=actor_id,
                actor_type=actor_type,
                at=now_utc_iso(),
                payload=event_payload,
            )
        )
        self._durable_commit(f"transition {card_id} {from_status}->{to_status}")
        got = self.get_card(card_id, tenant_id=tenant_id)
        assert got is not None
        return got

    def apply_executor_result(
        self,
        card_id: str,
        *,
        tenant_id: str = DEFAULT_TENANT,
        body_md: str | None = None,
        fields: dict[str, Any] | None = None,
        event: CardEvent | None = None,
    ) -> CardRecord:
        record = self.get_card(card_id, tenant_id=tenant_id)
        if record is None:
            raise CardNotFound(f"card {card_id!r} (tenant {tenant_id!r})")
        if fields:
            _apply_fields(record, fields)
        if body_md is not None:
            record.body_md = body_md
        record.updated_at = now_utc_iso()
        self._update_card_row(record)
        if event is not None:
            event.tenant_id = tenant_id
            event.card_id = card_id
            self._insert_event_row(event)
        self._durable_commit(f"executor result {card_id}")
        got = self.get_card(card_id, tenant_id=tenant_id)
        assert got is not None
        return got

    # ---- events -------------------------------------------------------

    def append_event(self, event: CardEvent) -> CardEvent:
        stored = self._insert_event_row(event)
        self._durable_commit(f"event {stored.type} on {stored.card_id}")
        return stored

    def list_events(
        self, card_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> list[CardEvent]:
        rows = self._run_query(
            f"SELECT * FROM card_events WHERE tenant_id = {self._ph} "
            f"AND card_id = {self._ph} ORDER BY seq",
            (tenant_id, card_id),
        )
        return [_row_to_event(r) for r in rows]

    # ---- batches ------------------------------------------------------

    def create_batch(self, batch: Batch) -> Batch:
        self._run_write(
            f"INSERT INTO batches (tenant_id, batch_id, created, manifest) "
            f"VALUES ({self._ph}, {self._ph}, {self._ph}, {self._ph})",
            (
                batch.tenant_id,
                batch.batch_id,
                batch.created or now_utc_iso(),
                json.dumps(batch.manifest, sort_keys=True),
            ),
        )
        self._durable_commit(f"create batch {batch.batch_id}")
        got = self.get_batch(batch.batch_id, tenant_id=batch.tenant_id)
        assert got is not None
        return got

    def get_batch(
        self, batch_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> Batch | None:
        rows = self._run_query(
            f"SELECT * FROM batches WHERE tenant_id = {self._ph} "
            f"AND batch_id = {self._ph}",
            (tenant_id, batch_id),
        )
        if not rows:
            return None
        row = rows[0]
        manifest_raw = row.get("manifest") or "{}"
        return Batch(
            batch_id=str(row["batch_id"]),
            tenant_id=str(row["tenant_id"]),
            created=str(row["created"]) if row.get("created") else None,
            manifest=json.loads(manifest_raw),
        )

    def next_batch_id(self, *, tenant_id: str = DEFAULT_TENANT) -> str:
        value = self._bump_counter(tenant_id, BATCH_COUNTER_NAME)
        return f"b{value:03d}"

    # ---- dependencies -------------------------------------------------

    def add_dependency(
        self,
        card_id: str,
        depends_on_id: str,
        *,
        tenant_id: str = DEFAULT_TENANT,
    ) -> None:
        if depends_on_id in self.get_dependencies(card_id, tenant_id=tenant_id):
            return
        self._insert_dependency_row(tenant_id, card_id, depends_on_id)
        self._durable_commit(f"dependency {card_id}->{depends_on_id}")

    def get_dependencies(
        self, card_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> list[str]:
        rows = self._run_query(
            f"SELECT depends_on_id FROM dependencies WHERE tenant_id = {self._ph} "
            f"AND card_id = {self._ph} ORDER BY depends_on_id",
            (tenant_id, card_id),
        )
        return [str(r["depends_on_id"]) for r in rows]

    def get_dependents(
        self, card_id: str, *, tenant_id: str = DEFAULT_TENANT
    ) -> list[str]:
        rows = self._run_query(
            f"SELECT card_id FROM dependencies WHERE tenant_id = {self._ph} "
            f"AND depends_on_id = {self._ph} ORDER BY card_id",
            (tenant_id, card_id),
        )
        return [str(r["card_id"]) for r in rows]

    # ---- shared row writers ------------------------------------------
    # These do not commit; the caller drives `_durable_commit` so one
    # logical operation is one durable unit.

    def _insert_card_row(self, record: CardRecord) -> None:
        row = card_record_to_row(record)
        cols = ", ".join(CARD_COLUMNS)
        placeholders = ", ".join(self._ph for _ in CARD_COLUMNS)
        self._run_write(
            f"INSERT INTO cards ({cols}) VALUES ({placeholders})",
            tuple(row[c] for c in CARD_COLUMNS),
        )

    def _update_card_row(self, record: CardRecord) -> None:
        row = card_record_to_row(record)
        mutable = [c for c in CARD_COLUMNS if c not in ("tenant_id", "card_id")]
        assignments = ", ".join(f"{c} = {self._ph}" for c in mutable)
        params = [row[c] for c in mutable]
        params.extend([record.tenant_id, record.card_id])
        self._run_write(
            f"UPDATE cards SET {assignments} "
            f"WHERE tenant_id = {self._ph} AND card_id = {self._ph}",
            tuple(params),
        )

    def _insert_event_row(self, event: CardEvent) -> CardEvent:
        event.event_id = event.event_id or uuid.uuid4().hex
        event.seq = self._next_event_seq(event.tenant_id, event.card_id)
        event.at = event.at or now_utc_iso()
        self._run_write(
            f"INSERT INTO card_events "
            f"(event_id, tenant_id, card_id, seq, type, actor_id, "
            f"actor_type, at, payload) VALUES "
            f"({', '.join(self._ph for _ in range(9))})",
            (
                event.event_id,
                event.tenant_id,
                event.card_id,
                event.seq,
                event.type,
                event.actor_id,
                event.actor_type,
                event.at,
                json.dumps(event.payload, sort_keys=True),
            ),
        )
        return event

    def _insert_dependency_row(
        self, tenant_id: str, card_id: str, depends_on_id: str
    ) -> None:
        self._run_write(
            f"INSERT INTO dependencies (tenant_id, card_id, depends_on_id) "
            f"VALUES ({self._ph}, {self._ph}, {self._ph})",
            (tenant_id, card_id, depends_on_id),
        )

    def _next_event_seq(self, tenant_id: str, card_id: str) -> int:
        rows = self._run_query(
            f"SELECT COALESCE(MAX(seq), 0) AS m FROM card_events "
            f"WHERE tenant_id = {self._ph} AND card_id = {self._ph}",
            (tenant_id, card_id),
        )
        return int(rows[0]["m"]) + 1

    def _bump_counter(self, tenant_id: str, name: str) -> int:
        """Increment and return a named monotonic counter.

        Seeds the row at 0 on first use. Subclasses run this inside
        whatever transaction discipline keeps it atomic for that
        engine.
        """
        rows = self._run_query(
            f"SELECT value FROM counters WHERE tenant_id = {self._ph} "
            f"AND name = {self._ph}",
            (tenant_id, name),
        )
        if not rows:
            self._run_write(
                f"INSERT INTO counters (tenant_id, name, value) "
                f"VALUES ({self._ph}, {self._ph}, 0)",
                (tenant_id, name),
            )
            current = 0
        else:
            current = int(rows[0]["value"])
        nxt = current + 1
        self._run_write(
            f"UPDATE counters SET value = {self._ph} "
            f"WHERE tenant_id = {self._ph} AND name = {self._ph}",
            (nxt, tenant_id, name),
        )
        self._durable_commit(f"counter {name} -> {nxt}")
        return nxt

    # ---- claim helpers shared by both stores -------------------------

    def _claim_fields(
        self, claimed_by: str, attempt_trace_id: str | None, now: str
    ) -> dict[str, Any]:
        """The frontmatter mutation a successful claim applies."""
        return {
            "status": CardStatus.ACTIVE.value,
            "claimed_by": claimed_by,
            "started_at": now,
            "last_heartbeat": now,
            "attempt_trace_id": attempt_trace_id or uuid.uuid4().hex,
        }

    def _claim_event(
        self, card_id: str, tenant_id: str, claimed_by: str, now: str
    ) -> CardEvent:
        return CardEvent(
            card_id=card_id,
            tenant_id=tenant_id,
            type=EventType.CLAIMED.value,
            actor_id=claimed_by,
            actor_type=ActorType.RUNNER.value,
            at=now,
            payload={"claimed_by": claimed_by},
        )


# --- module helpers --------------------------------------------------


def _apply_fields(record: CardRecord, fields: dict[str, Any]) -> None:
    """Apply a frontmatter-named field dict onto a `CardRecord`.

    Promoted fields land on their attribute; everything else lands in
    `frontmatter_extra`. `id` is rejected: a card cannot be renamed
    through a field update.
    """
    for key, value in fields.items():
        if key == "id":
            raise RepositoryError("card id is immutable")
        if hasattr(record, key) and key not in ("frontmatter_extra", "frontmatter_raw",
                                                 "body_md", "updated_at"):
            setattr(record, key, value)
        else:
            record.frontmatter_extra[key] = value


def _row_to_event(row: dict[str, Any]) -> CardEvent:
    payload_raw = row.get("payload") or "{}"
    return CardEvent(
        card_id=str(row["card_id"]),
        tenant_id=str(row["tenant_id"]),
        type=str(row["type"]),
        seq=int(row["seq"]),
        actor_id=str(row["actor_id"]) if row.get("actor_id") is not None else None,
        actor_type=str(row["actor_type"]),
        at=str(row["at"]),
        payload=json.loads(payload_raw),
        event_id=str(row["event_id"]),
    )


def _as_str_list(value: Any) -> list[str]:
    """Coerce a frontmatter list field to `list[str]`. None -> []."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(v) for v in value]
    return []
