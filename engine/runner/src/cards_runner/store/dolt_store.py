"""Dolt implementation of the card repository.

Dolt is the default store (storage_substrate_v2.md, as locked by
Drew). It is a SQL database with git-style versioning: every write
can be a commit, and branch/diff/merge are first-class. That buys two
things the design pass cared about: a `card_events` audit trail with
real version history behind it, and a claim primitive that is correct
by construction rather than by hoping `os.replace` is atomic.

The claim, precisely. A claimer:

1. Branches `main` to a short-lived `claim_<uuid>` branch.
2. Runs the guarded `UPDATE ... WHERE status = 'backlog'` on that
   branch and appends the `claimed` event.
3. Commits the branch and merges it back into `main`.

The merge is the arbiter. If two claimers race the same card, both
branch off a `backlog` row and both set `claimed_by` to different
values. The first merge into `main` is clean. The second sees `main`
already carrying a different `claimed_by` and Dolt rejects the merge
as a conflict. The loser catches that and re-plans, exactly as the
brief specifies. A claimer whose branch update affected zero rows
(the card was already active) never even reaches the merge.

This module talks to a `dolt sql-server` over the MySQL wire
protocol via PyMySQL. `DoltServer` manages the server process;
`DoltRepository` is a client. They are separate so a fleet of
runners can share one server, while a solo deployment can let one
repository own its server through `DoltRepository.embedded`.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from ..common.types import now_utc_iso
from .models import DEFAULT_TENANT, CardRecord
from .repository import CardNotFound, RepositoryError, _SqlCardRepository
from .schema import DIALECT_MYSQL

try:  # PyMySQL is an optional dependency; SQLite needs none of this.
    import pymysql
    import pymysql.cursors
    from pymysql.err import OperationalError as _MySQLOperationalError
except ImportError as exc:  # pragma: no cover - exercised only without the extra.
    raise RepositoryError(
        "the Dolt store needs PyMySQL: pip install cards-runner[dolt]"
    ) from exc


class DoltError(RepositoryError):
    """Raised for Dolt-specific failures (server boot, dolt binary)."""


def resolve_dolt_binary(explicit: str | None = None) -> str:
    """Find the `dolt` executable.

    Order: an explicit path, the `CARDS_DOLT_BIN` env var, then
    `dolt` on `PATH`. Raising here, early and clearly, beats a
    confusing failure deep inside a subprocess call.
    """
    candidate = explicit or os.environ.get("CARDS_DOLT_BIN")
    if candidate:
        if Path(candidate).is_file():
            return candidate
        raise DoltError(f"dolt binary not found at {candidate!r}")
    found = shutil.which("dolt")
    if found:
        return found
    raise DoltError(
        "dolt is not on PATH; install it or set CARDS_DOLT_BIN. "
        "Linux/macOS: see dolthub.com/downloads. Windows: winget install "
        "DoltHub.Dolt"
    )


def _free_port() -> int:
    """Return an unused loopback TCP port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _database_name(db_dir: Path) -> str:
    """Dolt's database name for a repo dir: the basename, non-word -> _."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", db_dir.name)


class DoltServer:
    """Manages a `dolt sql-server` process for one Dolt repository dir.

    Use as a context manager, or call `start()` / `stop()`. The
    server is the writable shared substrate; multiple
    `DoltRepository` clients connect to one server.
    """

    def __init__(
        self,
        db_dir: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        dolt_bin: str | None = None,
    ) -> None:
        self.db_dir = Path(db_dir)
        self.host = host
        self.port = port or _free_port()
        self._dolt_bin = resolve_dolt_binary(dolt_bin)
        self.database = _database_name(self.db_dir)
        self._proc: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> "DoltServer":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()

    def start(self, *, ready_timeout_sec: float = 30.0) -> None:
        """Init the repo if needed and boot the sql-server.

        Blocks until the server accepts a connection or the timeout
        expires.
        """
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_dolt_identity()
        if not (self.db_dir / ".dolt").is_dir():
            self._run_dolt(["init"])
        self._proc = subprocess.Popen(
            [
                self._dolt_bin, "sql-server",
                "--host", self.host,
                "--port", str(self.port),
            ],
            cwd=str(self.db_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_until_ready(ready_timeout_sec)

    def stop(self) -> None:
        """Terminate the server process."""
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive.
            self._proc.kill()
            self._proc.wait(timeout=5)
        self._proc = None

    def connection_kwargs(self) -> dict[str, Any]:
        """PyMySQL connect kwargs for a client of this server."""
        return {
            "host": self.host,
            "port": self.port,
            "user": "root",
            "password": "",
            "database": self.database,
        }

    def _ensure_dolt_identity(self) -> None:
        """Dolt refuses to init or commit without a name and email set."""
        existing = self._run_dolt(["config", "--global", "--list"], check=False)
        text = existing.stdout.decode("utf-8", "replace")
        if "user.name" not in text:
            self._run_dolt(
                ["config", "--global", "--add", "user.name", "cards-runner"]
            )
        if "user.email" not in text:
            self._run_dolt(
                ["config", "--global", "--add", "user.email",
                 "runner@agile-cards.local"]
            )

    def _run_dolt(
        self, args: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [self._dolt_bin, *args],
            cwd=str(self.db_dir),
            check=check,
            capture_output=True,
            timeout=60,
        )

    def _wait_until_ready(self, timeout_sec: float) -> None:
        deadline = time.monotonic() + timeout_sec
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise DoltError(
                    f"dolt sql-server exited early rc={self._proc.returncode}"
                )
            try:
                conn = pymysql.connect(
                    connect_timeout=2, **self.connection_kwargs()
                )
                conn.close()
                return
            except Exception as err:  # noqa: BLE001 - retry until ready.
                last_err = err
                time.sleep(0.25)
        raise DoltError(f"dolt sql-server never became ready: {last_err}")


class DoltRepository(_SqlCardRepository):
    """A card repository backed by a Dolt SQL server.

    Construct one per thread or process. PyMySQL connections are not
    shared across threads, and the claim's branch switching is
    per-session, so each runner gets its own repository and its own
    connection.
    """

    _dialect = DIALECT_MYSQL
    _ph = "%s"

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int,
        database: str,
        user: str = "root",
        password: str = "",
        owned_server: DoltServer | None = None,
    ) -> None:
        self._owned_server = owned_server
        self._conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )

    @classmethod
    def connect(cls, server: DoltServer) -> "DoltRepository":
        """Build a client repository for an already-running server."""
        return cls(**server.connection_kwargs())

    @classmethod
    def embedded(
        cls,
        db_dir: str | Path,
        *,
        port: int | None = None,
        dolt_bin: str | None = None,
    ) -> "DoltRepository":
        """Start a private server for `db_dir`, return a repo that owns it.

        The single-instance convenience path. `close()` stops the
        server. For a fleet, run one `DoltServer` and give each runner
        a `DoltRepository.connect(server)`.
        """
        server = DoltServer(db_dir, port=port, dolt_bin=dolt_bin)
        server.start()
        repo = cls(owned_server=server, **server.connection_kwargs())
        repo.initialize_schema()
        return repo

    # ---- subclass hooks ----------------------------------------------

    def _run_query(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def _run_write(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def _durable_commit(self, message: str) -> None:
        """Snapshot the working set as a Dolt commit.

        A Dolt commit is what makes a write visible to branches
        created afterward, so the branch/merge claim depends on this
        being called after every mutation. An empty working set is
        not an error here.
        """
        with self._conn.cursor() as cur:
            try:
                cur.execute("CALL DOLT_COMMIT('-A', '-m', %s)", (message,))
            except _MySQLOperationalError as err:
                if "nothing to commit" in str(err).lower():
                    return
                raise

    def _column_exists(self, table: str, column: str) -> bool:
        # `INFORMATION_SCHEMA.COLUMNS` works on Dolt (MySQL compat) and
        # is preferable to `SHOW COLUMNS` because it accepts a real
        # WHERE clause with placeholders instead of a LIKE pattern.
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE table_schema = DATABASE() "
                "AND table_name = %s AND column_name = %s",
                (table, column),
            )
            row = cur.fetchone() or {"n": 0}
            return int(row.get("n", 0)) > 0

    def close(self) -> None:
        try:
            self._conn.close()
        finally:
            if self._owned_server is not None:
                self._owned_server.stop()

    # ---- claim: a conditional update inside a Dolt transaction ------

    def claim_card(
        self,
        card_id: str,
        *,
        claimed_by: str,
        attempt_trace_id: str | None = None,
        tenant_id: str = DEFAULT_TENANT,
    ) -> CardRecord | None:
        """Claim a backlog card via a Dolt SQL transaction.

        A Dolt SQL transaction is itself a short-lived branch that
        merges into the branch HEAD on COMMIT. This is the brief's
        "update on a branch then merge" expressed as the native Dolt
        primitive, rather than explicit DOLT_BRANCH and DOLT_MERGE
        calls, which, run per claim from many sessions, fight over
        the shared working set and fail in ways that are not the
        clean merge conflict the design expected.

        Two claimers both run the guarded UPDATE inside their own
        transaction. The first to COMMIT wins. The second either saw
        the card already active (affected rows 0) or has its COMMIT
        rejected with a serialization failure when Dolt merges the
        transaction against the now-changed row. Both outcomes mean
        the claim was lost, and both return None.
        """
        now = now_utc_iso()
        fields = self._claim_fields(claimed_by, attempt_trace_id, now)
        self._conn.begin()
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE cards SET status = %s, claimed_by = %s, "
                    "started_at = %s, last_heartbeat = %s, "
                    "attempt_trace_id = %s, updated_at = %s "
                    "WHERE tenant_id = %s AND card_id = %s "
                    "AND status = 'backlog'",
                    (
                        fields["status"], fields["claimed_by"],
                        fields["started_at"], fields["last_heartbeat"],
                        fields["attempt_trace_id"], now, tenant_id, card_id,
                    ),
                )
                affected = cur.rowcount
            if affected != 1:
                # Not in backlog: already claimed, or gone.
                self._conn.rollback()
                return None
            self._insert_event_row(
                self._claim_event(card_id, tenant_id, claimed_by, now)
            )
            self._conn.commit()
        except _MySQLOperationalError as err:
            self._conn.rollback()
            if _is_lost_claim_race(err):
                # The transaction merge conflicted with a claimer
                # that committed first. We lost the race.
                return None
            raise
        except Exception:
            self._conn.rollback()
            raise
        # Won. Snapshot into Dolt history; the working set is already
        # authoritative for queries.
        self._durable_commit(f"claim {card_id} by {claimed_by}")
        claimed = self.get_card(card_id, tenant_id=tenant_id)
        if claimed is None:  # pragma: no cover - just committed it.
            raise CardNotFound(f"card {card_id!r} vanished mid-claim")
        return claimed


# Dolt error codes and message fragments that mean a claim transaction
# lost a concurrency race rather than hitting a real fault.
_LOST_RACE_CODE = 1213  # serialization failure.
_LOST_RACE_MARKERS = (
    "serialization failure",
    "conflict",
    "would be stomped",
    "not ancestor",
)


def _is_lost_claim_race(err: _MySQLOperationalError) -> bool:
    """True when a Dolt OperationalError means the claim lost a race."""
    code = err.args[0] if err.args else None
    if code == _LOST_RACE_CODE:
        return True
    message = str(err).lower()
    return any(marker in message for marker in _LOST_RACE_MARKERS)
