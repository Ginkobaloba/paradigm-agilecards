"""SQLite implementation of the card repository.

SQLite is the de-risking fallback and the minimal-deploy option: it
is stdlib, needs no server, no install, and no network. For a solo
user it is strictly better than the v1 filesystem substrate, since it
adds real transactions and real queries while staying a single
portable file (storage_substrate_v2.md section 4.1).

The claim primitive is a guarded conditional `UPDATE`. Two claimers
both run `UPDATE ... WHERE status = 'backlog'`; SQLite serializes the
writers, so exactly one sees an affected-row count of 1 and the other
sees 0. No `SELECT` first, no `BEGIN IMMEDIATE` needed: the
conditional update is itself the arbiter. The busy timeout makes the
loser wait for the winner's commit rather than raising
`database is locked`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..common.types import now_utc_iso
from .models import DEFAULT_TENANT, CardRecord
from .repository import CardNotFound, _SqlCardRepository
from .schema import DIALECT_SQLITE


class SqliteRepository(_SqlCardRepository):
    """A card repository backed by a single SQLite file.

    Construct one per process (or per thread); SQLite's own file
    locking provides cross-connection and cross-process safety, which
    is the whole point of moving the claim off the filesystem.
    """

    _dialect = DIALECT_SQLITE
    _ph = "?"

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_sec: float = 30.0,
    ) -> None:
        self._db_path = str(db_path)
        # Default (deferred) isolation: DML auto-opens a transaction
        # and `_durable_commit` is the COMMIT. `timeout` is the busy
        # timeout, so a contended writer waits instead of erroring.
        self._conn = sqlite3.connect(
            self._db_path,
            timeout=busy_timeout_sec,
            check_same_thread=True,
        )
        self._conn.row_factory = sqlite3.Row
        if self._db_path != ":memory:":
            # WAL lets readers run while a writer holds the lock. It is
            # unavailable for in-memory databases, hence the guard.
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def open(
        cls, db_path: str | Path, *, busy_timeout_sec: float = 30.0
    ) -> "SqliteRepository":
        """Construct a repository and initialize the schema in one call."""
        repo = cls(db_path, busy_timeout_sec=busy_timeout_sec)
        repo.initialize_schema()
        return repo

    # ---- subclass hooks ----------------------------------------------

    def _run_query(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        cur = self._conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(row) for row in rows]

    def _run_write(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        cur = self._conn.execute(sql, params)
        return cur.rowcount

    def _durable_commit(self, message: str) -> None:
        # SQLite has no commit message; the message is for Dolt parity.
        del message
        self._conn.commit()

    def _column_exists(self, table: str, column: str) -> bool:
        # `PRAGMA table_info(...)` is the supported way to read SQLite
        # column lists without parsing `sqlite_master.sql`. Returns one
        # row per column with `name` in slot 1.
        cur = self._conn.execute(f"PRAGMA table_info({table})")
        names = {row["name"] for row in cur.fetchall()}
        return column in names

    def close(self) -> None:
        self._conn.close()

    # ---- claim --------------------------------------------------------

    def claim_card(
        self,
        card_id: str,
        *,
        claimed_by: str,
        attempt_trace_id: str | None = None,
        tenant_id: str = DEFAULT_TENANT,
    ) -> CardRecord | None:
        now = now_utc_iso()
        fields = self._claim_fields(claimed_by, attempt_trace_id, now)
        try:
            # The conditional UPDATE is the arbiter. Affected rows == 1
            # means we won; 0 means the card was already claimed, gone,
            # or taken by a concurrent claimer.
            cur = self._conn.execute(
                "UPDATE cards SET status = ?, claimed_by = ?, started_at = ?, "
                "last_heartbeat = ?, attempt_trace_id = ?, updated_at = ? "
                "WHERE tenant_id = ? AND card_id = ? AND status = 'backlog'",
                (
                    fields["status"],
                    fields["claimed_by"],
                    fields["started_at"],
                    fields["last_heartbeat"],
                    fields["attempt_trace_id"],
                    now,
                    tenant_id,
                    card_id,
                ),
            )
            if cur.rowcount != 1:
                self._conn.rollback()
                return None
            # We hold the write lock until commit; append the claim
            # event in the same transaction so the claim and its audit
            # row land together or not at all.
            self._insert_event_row(
                self._claim_event(card_id, tenant_id, claimed_by, now)
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        claimed = self.get_card(card_id, tenant_id=tenant_id)
        if claimed is None:  # pragma: no cover - just updated it.
            raise CardNotFound(f"card {card_id!r} vanished mid-claim")
        return claimed
