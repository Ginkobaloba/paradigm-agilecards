"""Alembic environment.

Migrations run with an owner/admin DSN (PARADIGM_DATABASE_MIGRATE_URL): they
create roles, RLS policies, and triggers, which the runtime ``agilecards_app``
role deliberately lacks the privileges to do. The runtime app never runs
migrations."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from cards_api.models import Base

target_metadata = Base.metadata


def _database_url() -> str:
    url = (
        context.config.get_main_option("sqlalchemy.url_override", None)
        or os.environ.get("PARADIGM_DATABASE_MIGRATE_URL")
        or ""
    )
    if not url or "OVERRIDDEN_IN_ENV_PY" in url:
        raise RuntimeError(
            "Set PARADIGM_DATABASE_MIGRATE_URL to an owner/admin Postgres DSN "
            "(migrations manage roles and RLS policies)."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
