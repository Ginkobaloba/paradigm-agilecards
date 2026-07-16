"""Shared test fixtures: offline JWKS auth + a real migrated Postgres.

Auth (AC-CARDS-003): an in-process RSA keypair and a fake JWK client keep every
auth path (valid / expired / tampered / wrong-alg / wrong-iss / wrong-aud)
deterministic and offline -- unchanged from K11.

Persistence (ADR-2026-07-16): row-level security can only be proven against
real Postgres, so the suite runs against one. Locally that is the disposable
dev container (see backend/README.md):

    docker run -d --name agilecards-pg-dev -e POSTGRES_PASSWORD=devpassword \
        -e POSTGRES_DB=agilecards -p 5440:5432 postgres:16-alpine

CI provides a service container. A fresh, uniquely-named database is created
per test session, migrated to head with Alembic, and dropped afterwards. Tests
connect as the ``agilecards_app`` role (NOSUPERUSER/NOBYPASSRLS) -- the same
privilege level as production -- so RLS is actually in force under test.

These fixtures fail loudly (never skip) when Postgres is unreachable: silently
skipping the security suite is exactly the CI-masking failure the 2026-07-16
audit flagged (S1).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import jwt
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ISSUER = "https://auth.paradigm.codes"
AUDIENCE = "paradigm-agilecards"
KID = "test-key-1"

# Two orgs used across the isolation tests (AC-CARDS-007).
ORG_A = "org_acme"
ORG_B = "org_globex"

BACKEND_DIR = Path(__file__).resolve().parents[1]

_DEFAULT_ADMIN_DSN = "postgresql+psycopg://postgres:devpassword@localhost:5440/postgres"
ADMIN_DSN = os.environ.get("AGILECARDS_TEST_ADMIN_DSN", _DEFAULT_ADMIN_DSN)
APP_ROLE = "agilecards_app"
APP_ROLE_PASSWORD = "agilecards_test_app_pw"  # test-database-only credential


# --------------------------------------------------------------------------
# Auth fixtures (offline JWKS)
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def rsa_keypair():
    """A single RSA keypair reused across the session (key-gen is slow)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {
        "private_pem": private_pem.decode(),
        "public_pem": public_pem.decode(),
    }


class _FakeSigningKey:
    """Mimics PyJWK: jwt.decode() reads ``.key`` for the verifying key."""

    def __init__(self, key: str) -> None:
        self.key = key


class _FakeJWKClient:
    """Stand-in for PyJWKClient.

    Selects the verifying key by ``kid`` the same way the real client does, so
    a token carrying an unknown ``kid`` raises -- which the verifier maps to a
    rejection, mirroring production behavior without any network fetch.
    """

    def __init__(self, keys_by_kid: dict[str, str]) -> None:
        self._keys_by_kid = keys_by_kid

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if kid not in self._keys_by_kid:
            raise jwt.PyJWTError(f"no signing key for kid={kid!r}")
        return _FakeSigningKey(self._keys_by_kid[kid])


@pytest.fixture
def jwk_client(rsa_keypair) -> _FakeJWKClient:
    return _FakeJWKClient({KID: rsa_keypair["public_pem"]})


@pytest.fixture
def verifier(jwk_client):
    """A TokenVerifier wired to the offline mock JWK client."""
    from cards_api.auth import TokenVerifier

    return TokenVerifier(issuer=ISSUER, audience=AUDIENCE, jwk_client=jwk_client)


@pytest.fixture
def make_token(rsa_keypair):
    """Factory minting tokens. Defaults produce a valid Paradigm access token."""
    import time

    def _make(
        *,
        org_id: str = ORG_A,
        roles: list[str] | None = None,
        sub: str = "user_123",
        issuer: str = ISSUER,
        audience: str = AUDIENCE,
        exp_offset: int = 3600,
        nbf_offset: int = 0,
        alg: str = "RS256",
        key: str | None = None,
        kid: str | None = KID,
        drop_claims: tuple[str, ...] = (),
    ) -> str:
        now = int(time.time())
        payload = {
            "sub": sub,
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "nbf": now + nbf_offset,
            "exp": now + exp_offset,
            "org_id": org_id,
            "roles": roles if roles is not None else ["member"],
        }
        for claim in drop_claims:
            payload.pop(claim, None)
        signing_key = key if key is not None else rsa_keypair["private_pem"]
        headers = {"kid": kid} if kid is not None else {}
        return jwt.encode(payload, signing_key, algorithm=alg, headers=headers)

    return _make


@pytest.fixture
def auth_headers(make_token):
    """Factory: Authorization headers for a valid token (kwargs pass through)."""

    def _headers(**kwargs) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(**kwargs)}"}

    return _headers


# --------------------------------------------------------------------------
# Postgres fixtures (real database, migrated, app-role connection)
# --------------------------------------------------------------------------

_ALL_TABLES = (
    "staged_cards",
    "story_batches",
    "sprint_cards",
    "sprints",
    "retros",
    "saved_views",
    "card_events",
    "card_rank",
    "cards",
    "audit_events",
)


@pytest.fixture(scope="session")
def pg_urls():
    """Create a unique test database, migrate it, bootstrap the app role."""
    admin_url = make_url(ADMIN_DSN)
    dbname = f"agilecards_test_{uuid.uuid4().hex[:10]}"

    try:
        control = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with control.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    except Exception as exc:  # noqa: BLE001 - we want ONE clear message here
        pytest.fail(
            "Cannot reach the test Postgres at "
            f"{admin_url.render_as_string(hide_password=True)!r}: {exc}\n"
            "Start it with:\n"
            "  docker run -d --name agilecards-pg-dev -e POSTGRES_PASSWORD=devpassword "
            "-e POSTGRES_DB=agilecards -p 5440:5432 postgres:16-alpine\n"
            "or point AGILECARDS_TEST_ADMIN_DSN at an admin-capable Postgres.\n"
            "(This suite fails rather than skips: it contains the RLS/security "
            "tests and must never be silently green.)"
        )

    db_admin_url = admin_url.set(database=dbname)
    db_admin_str = db_admin_url.render_as_string(hide_password=False)

    alembic_cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    previous = os.environ.get("PARADIGM_DATABASE_MIGRATE_URL")
    os.environ["PARADIGM_DATABASE_MIGRATE_URL"] = db_admin_str
    try:
        command.upgrade(alembic_cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("PARADIGM_DATABASE_MIGRATE_URL", None)
        else:
            os.environ["PARADIGM_DATABASE_MIGRATE_URL"] = previous

    admin_engine = create_engine(db_admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f"ALTER ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE_PASSWORD}'"))

    app_url = db_admin_url.set(username=APP_ROLE, password=APP_ROLE_PASSWORD)

    yield {
        "app": app_url.render_as_string(hide_password=False),
        "admin": db_admin_str,
        "admin_engine": admin_engine,
    }

    admin_engine.dispose()
    with control.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{dbname}" WITH (FORCE)'))
    control.dispose()


@pytest.fixture(scope="session")
def app_engine(pg_urls):
    """Engine connected as agilecards_app -- production privilege level."""
    engine = create_engine(pg_urls["app"], pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def database(pg_urls, app_engine):
    """A clean, migrated Database per test (all tables truncated up front)."""
    from cards_api.db import Database

    with pg_urls["admin_engine"].connect() as conn:
        conn.execute(
            text(f"TRUNCATE {', '.join(_ALL_TABLES)} RESTART IDENTITY CASCADE")
        )
    return Database(engine=app_engine)


@pytest.fixture
def org_session(database):
    """Factory for org-bound sessions, for seeding and direct assertions:

    with org_session(ORG_A) as s:
        s.add(Card(org_id=ORG_A, id="a1", ...))
    """

    def _factory(org_id: str):
        return database.org_session(org_id)

    return _factory


@pytest.fixture
def client(verifier, database):
    """TestClient over an app using the offline verifier and the migrated DB."""
    from fastapi.testclient import TestClient

    from cards_api.main import create_app

    app = create_app(verifier=verifier, database=database)
    return TestClient(app)
