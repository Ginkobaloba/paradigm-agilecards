"""Alembic environment. Online-only: the migrations are raw SQL whose whole
point is the RLS/grant side effects, so generating offline SQL scripts is
deliberately unsupported."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool


def _database_url() -> str:
    url = context.config.get_main_option("sqlalchemy.url")
    if url:
        return url
    env_url = os.environ.get("CARDS_MIGRATIONS_DATABASE_URL") or os.environ.get(
        "CARDS_DATABASE_URL"
    )
    if not env_url:
        raise RuntimeError(
            "Set CARDS_MIGRATIONS_DATABASE_URL (owner role) to run migrations."
        )
    return env_url


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported for this project.")

engine = create_engine(_database_url(), poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
