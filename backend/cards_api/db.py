"""Database wiring: engine, sessions, and the org-scoped request context.

The one non-negotiable invariant here (ADR D3): every session used to touch
tenant data first sets the transaction-local GUC ``app.current_org`` to the
org_id of the *verified token*. The RLS policies key off that setting;
``set_config(..., is_local := true)`` scopes it to the transaction, so pooled
connections can never leak an org across requests.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Sentinel org for audit rows written outside any verified org context
# (authentication failures). Never a real tenant; unreadable through the API.
SYSTEM_ORG = "__system__"


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def bind_org(session: Session, org_id: str) -> None:
    """Set the transaction-local org context RLS policies match against."""
    session.execute(
        text("SELECT set_config('app.current_org', :org, true)"), {"org": org_id}
    )


@contextmanager
def org_session(
    session_factory: sessionmaker[Session], org_id: str
) -> Iterator[Session]:
    """A transaction bound to ``org_id``. Commits on success, rolls back on
    error. All repository access goes through one of these."""
    with session_factory() as session:
        with session.begin():
            bind_org(session, org_id)
            yield session
