"""Database plumbing: engine, sessions, and the org-scoped RLS context.

The security contract lives here (ADR-2026-07-16). Every tenant-facing request
runs inside a transaction that first binds the verified token's ``org_id`` to
the Postgres GUC ``app.current_org`` via ``set_config(..., is_local => true)``.
Row-level-security policies on every tenant table compare ``org_id`` against
that GUC, so the *database* -- not application filtering -- decides which rows
exist for this request. ``SET LOCAL`` semantics scope the binding to the
transaction, so pooled connections cannot leak an org context between requests.

Fail-closed behavior: if no org context is bound, the policies compare against
NULL and match zero rows. A request that skips the binding sees an empty
tenant, never someone else's.

The app connects as ``agilecards_app`` (NOSUPERUSER, NOBYPASSRLS, no DDL).
Superuser or table-owner connections would bypass or weaken RLS -- do not point
``PARADIGM_DATABASE_URL`` at one outside of migrations.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

_SET_ORG = text("SELECT set_config('app.current_org', :org_id, true)")


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def bind_org(session: Session, org_id: str) -> None:
    """Bind ``org_id`` as the RLS context for the session's current transaction."""
    session.execute(_SET_ORG, {"org_id": org_id})


class Database:
    """Owns the engine and hands out transaction-scoped, org-bound sessions."""

    def __init__(self, database_url: str | None = None, *, engine: Engine | None = None) -> None:
        if engine is None and not database_url:
            raise ValueError("Database requires database_url or engine")
        self.engine = engine if engine is not None else make_engine(database_url or "")
        self._session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    @contextmanager
    def org_session(self, org_id: str) -> Iterator[Session]:
        """A transaction with RLS bound to ``org_id``. Commits on clean exit,
        rolls back on any exception."""
        session = self._session_factory()
        try:
            with session.begin():
                bind_org(session, org_id)
                yield session
        finally:
            session.close()

    @contextmanager
    def system_session(self) -> Iterator[Session]:
        """A transaction with NO org context.

        RLS fails closed here: tenant tables show zero rows. The only intended
        use is inserting pre-auth ``audit_events`` rows (org_id NULL), whose
        INSERT policy explicitly allows that case.
        """
        session = self._session_factory()
        try:
            with session.begin():
                yield session
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
