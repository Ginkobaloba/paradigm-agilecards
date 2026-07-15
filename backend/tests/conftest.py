"""Shared test fixtures for the AgileCards backend auth suite.

The chunk requires JWKS verification that is *mockable for tests* (AC-CARDS-003):
no network, no real IdP. We generate an RSA keypair in-process, expose it as a
JWKS, and mint RS256 tokens signed with the private half. The verifier under
test is pointed at a fake JWK client that serves that public key, so every
auth path (valid / expired / tampered / wrong-alg / wrong-iss / wrong-aud) is
exercised deterministically and offline.
"""

from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://auth.paradigm.codes"
AUDIENCE = "paradigm-agilecards"
KID = "test-key-1"

# Two orgs used across the isolation tests (AC-CARDS-007).
ORG_A = "org_acme"
ORG_B = "org_globex"


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
def seeded_store():
    """A store with deterministic cards across two orgs for isolation tests."""
    from cards_api.store import Card, CardStore

    store = CardStore()
    store.add(Card(id="a1", org_id=ORG_A, title="Acme: ship login"))
    store.add(Card(id="a2", org_id=ORG_A, title="Acme: fix nav"))
    store.add(Card(id="b1", org_id=ORG_B, title="Globex: invoice run"))
    return store


@pytest.fixture
def client(verifier, seeded_store):
    """TestClient over an app using the offline verifier and seeded store."""
    from fastapi.testclient import TestClient

    from cards_api.main import create_app

    app = create_app(verifier=verifier, store=seeded_store)
    return TestClient(app)


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
