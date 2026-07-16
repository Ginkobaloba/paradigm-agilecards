"""Postgres integration-test harness.

The RLS/persistence tests in this package run against a REAL Postgres —
row-level security cannot be faked on SQLite or in-memory stores. Server
resolution order:

1. ``CARDS_TEST_ADMIN_URL`` env var — a superuser URL. CI sets this to the
   GitHub Actions postgres service container.
2. A throwaway local Docker container (``postgres:16-alpine``) started for
   the session and removed afterwards.

If neither is available these tests **fail** (not skip), with instructions.
A silently-skipped security suite is exactly the "CI green means nothing"
failure mode the 2026-07-16 audit flagged (S1) — we don't reproduce it here.

Roles created (mirrors the production posture, see the ADR):
- ``cards_owner`` — LOGIN, non-superuser; owns the schema, runs migrations.
- ``cards_app``   — LOGIN, NOSUPERUSER NOBYPASSRLS, not the owner; what the
  application connects as. Created NOLOGIN by the migration itself; the
  harness only attaches LOGIN + a password.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

ADMIN_ENV = "CARDS_TEST_ADMIN_URL"
OWNER_PW = "owner_test_pw"
APP_PW = "app_test_pw"

BACKEND_DIR = Path(__file__).resolve().parents[2]

_NO_PG_MSG = (
    "Postgres integration tests need a server. Either set "
    f"{ADMIN_ENV}=postgresql+psycopg://postgres:<pw>@host:port/postgres "
    "or have Docker available (the harness starts postgres:16-alpine itself)."
)


def _wait_for_pg(url: str, timeout: float = 60.0) -> None:
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            engine.dispose()
            return
        except Exception as exc:  # noqa: BLE001 - retry loop by design
            last_err = exc
            time.sleep(0.5)
    engine.dispose()
    raise RuntimeError(f"Postgres did not become ready: {last_err}")


@pytest.fixture(scope="session")
def pg_admin_url():
    """A superuser URL to a running Postgres (env-provided or dockerized)."""
    env_url = os.environ.get(ADMIN_ENV)
    if env_url:
        _wait_for_pg(env_url)
        yield env_url
        return

    run = subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "-e", "POSTGRES_PASSWORD=postgres",
            "-p", "127.0.0.1:0:5432",
            "postgres:16-alpine",
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        pytest.fail(f"{_NO_PG_MSG}\n(docker run failed: {run.stderr.strip()})")
    container_id = run.stdout.strip()
    try:
        port_out = subprocess.run(
            ["docker", "port", container_id, "5432/tcp"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()[0].strip()
        host_port = port_out.rsplit(":", 1)[1]
        url = f"postgresql+psycopg://postgres:postgres@127.0.0.1:{host_port}/postgres"
        _wait_for_pg(url)
        yield url
    finally:
        subprocess.run(["docker", "stop", container_id], capture_output=True)


@pytest.fixture(scope="session")
def pg_urls(pg_admin_url):
    """Provision roles + a fresh database, run migrations, return URLs.

    Returns a dict: ``owner`` (schema owner / migrations), ``app`` (the
    NOBYPASSRLS application role), ``dbname``.
    """
    admin = sa.create_engine(pg_admin_url, isolation_level="AUTOCOMMIT")
    dbname = f"cards_test_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(sa.text(
            "DO $$ BEGIN IF NOT EXISTS "
            "(SELECT 1 FROM pg_roles WHERE rolname = 'cards_owner') THEN "
            "CREATE ROLE cards_owner LOGIN NOSUPERUSER; END IF; END $$"
        ))
        # CREATEROLE because the initial migration creates cards_app itself
        # (same requirement is documented for deploy provisioning).
        conn.execute(sa.text(
            f"ALTER ROLE cards_owner LOGIN CREATEROLE PASSWORD '{OWNER_PW}'"
        ))
        conn.execute(sa.text(f'CREATE DATABASE "{dbname}" OWNER cards_owner'))

    def _url(user: str, pw: str) -> str:
        base = sa.engine.make_url(pg_admin_url)
        # str(URL) masks the password; render explicitly.
        return base.set(username=user, password=pw, database=dbname).render_as_string(
            hide_password=False
        )

    owner_url = _url("cards_owner", OWNER_PW)

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", owner_url)
    command.upgrade(cfg, "head")

    # The migration creates cards_app as NOLOGIN (it never sets credentials —
    # those are deploy/harness concerns). Attach LOGIN + password here.
    with admin.connect() as conn:
        conn.execute(sa.text(f"ALTER ROLE cards_app LOGIN PASSWORD '{APP_PW}'"))
    admin.dispose()

    return {"owner": owner_url, "app": _url("cards_app", APP_PW), "dbname": dbname}


@pytest.fixture(scope="session")
def owner_engine(pg_urls):
    engine = sa.create_engine(pg_urls["owner"], poolclass=sa.pool.NullPool)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(pg_urls):
    engine = sa.create_engine(pg_urls["app"], poolclass=sa.pool.NullPool)
    yield engine
    engine.dispose()


TENANT_TABLES = (
    "cards",
    "card_ranks",
    "card_events",
    "saved_views",
    "sprints",
    "sprint_cards",
    "retros",
    "triage_batches",
    "triage_cards",
    "audit_events",
)


@pytest.fixture(autouse=True)
def pg_clean(owner_engine):
    """Truncate all tenant tables after each test (TRUNCATE ignores RLS and
    is an owner privilege, so this is the one sanctioned bypass)."""
    yield
    with owner_engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {', '.join(TENANT_TABLES)} CASCADE"))


def set_org(conn, org_id: str) -> None:
    """Transaction-local org context, exactly as the app sets it."""
    conn.execute(
        sa.text("SELECT set_config('app.current_org', :org, true)"), {"org": org_id}
    )
