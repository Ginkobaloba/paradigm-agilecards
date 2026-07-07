"""CARDS-013 -- consumer contract test for ``@paradigm/auth`` (chunk K16).

``@paradigm/auth`` is TypeScript-only; per the integration roadmap there is no
Python ``@paradigm/auth`` package in v1. The Paradigm AgileCards FastAPI backend
instead verifies bearer JWTs *directly* against the IdP JWKS (RS256, PyJWT). This
file is the **executable contract** that pins what the IdP / ``@paradigm/auth``
issues and what any Paradigm Python consumer MUST enforce:

  * RS256 signatures only -- symmetric ("alg confusion") and ``none`` are rejected.
  * ``kid``-based key resolution from the JWKS.
  * JWKS *refresh* on an unknown ``kid`` (key rotation), fetched exactly once.
  * Expired tokens are rejected.
  * Tampered signatures are rejected.

It is self-contained on purpose: it stands up an in-memory RSA keypair + JWKS and
a reference verifier, so it runs offline on every PR and on every Renovate bump
of a ``@paradigm/*`` package (the K16 ``contracts`` CI job). The production
verifier is owned by chunk K11 (AC-CARDS-003); when it lands it must satisfy the
same contract exercised here. The copy-paste verifier sample is owned by K-DOC
(``docs/runbooks/python-jwt-verification.md``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://auth.paradigm.codes"
AUDIENCE = "paradigm-agilecards"
PRIMARY_KID = "paradigm-key-2026"
ROTATED_KID = "paradigm-key-2026-next"


class TokenError(Exception):
    """Raised when a token fails the contract. The message is a stable code."""


class JwksSource:
    """Stands in for the IdP JWKS endpoint -- holds the currently published keys.

    ``fetch_count`` lets the tests prove that an unknown ``kid`` triggers exactly
    one refresh (no network fan-out, no retry loop).
    """

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}
        self.fetch_count = 0

    def publish(self, kid: str, public_pem: str) -> None:
        self._keys[kid] = public_pem

    def fetch(self) -> dict[str, str]:
        self.fetch_count += 1
        return dict(self._keys)


class ContractVerifier:
    """Reference JWKS verifier embodying the ``@paradigm/auth`` token contract.

    Resolves the verifying key by ``kid`` from a cached JWKS; on a cache miss it
    refreshes the JWKS exactly once (covering IdP key rotation) before rejecting.
    Decoding pins ``RS256`` so the public-key-as-HMAC-secret confusion attack and
    ``alg: none`` are both impossible.
    """

    def __init__(self, source: JwksSource, *, issuer: str, audience: str) -> None:
        self._source = source
        self._issuer = issuer
        self._audience = audience
        self._cache: dict[str, str] = {}

    def _resolve(self, kid: str) -> str | None:
        if kid in self._cache:
            return self._cache[kid]
        # Cache miss: the kid may be a freshly rotated signing key. Refresh the
        # JWKS once, then look again. Still missing -> the caller rejects.
        self._cache = self._source.fetch()
        return self._cache.get(kid)

    def verify(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenError("malformed_token") from exc

        kid = header.get("kid")
        if not kid:
            raise TokenError("missing_kid")

        key = self._resolve(kid)
        if key is None:
            raise TokenError("unknown_kid")

        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("token_expired") from exc
        except jwt.InvalidAlgorithmError as exc:
            raise TokenError("bad_algorithm") from exc
        except jwt.InvalidSignatureError as exc:
            raise TokenError("bad_signature") from exc
        except jwt.PyJWTError as exc:
            raise TokenError("invalid_token") from exc


def _generate_keypair() -> dict[str, str]:
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
    return {"private_pem": private_pem.decode(), "public_pem": public_pem.decode()}


def _mint(
    private_pem: str,
    *,
    kid: str | None,
    alg: str = "RS256",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    exp_offset: int = 3600,
    org_id: str = "org_acme",
    roles: tuple[str, ...] = ("admin", "member"),
    sub: str = "user_123",
    drop: tuple[str, ...] = (),
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + exp_offset,
        "org_id": org_id,
        "roles": list(roles),
    }
    for claim in drop:
        payload.pop(claim, None)
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(payload, private_pem, algorithm=alg, headers=headers)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _forge_hs256(payload: dict, *, secret: str, kid: str) -> str:
    """Hand-roll an HS256 token (PyJWT refuses a PEM key as an HMAC secret).

    This is the classic algorithm-confusion forgery: claim ``alg: HS256`` and
    sign with the IdP's *public* key as the shared secret, betting the verifier
    will hand that public key to an HMAC check. A verifier that pins RS256 never
    reaches signature verification, so the secret is irrelevant -- it is rejected
    on the algorithm alone.
    """
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(payload).encode())}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


# --------------------------------------------------------------------------- #
# Fixtures (RSA key-gen is slow, so keys are session-scoped).
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def primary_key() -> dict[str, str]:
    return _generate_keypair()


@pytest.fixture(scope="session")
def rotated_key() -> dict[str, str]:
    return _generate_keypair()


@pytest.fixture
def source(primary_key: dict[str, str]) -> JwksSource:
    src = JwksSource()
    src.publish(PRIMARY_KID, primary_key["public_pem"])
    return src


@pytest.fixture
def verifier(source: JwksSource) -> ContractVerifier:
    return ContractVerifier(source, issuer=ISSUER, audience=AUDIENCE)


# --------------------------------------------------------------------------- #
# Contract assertions.
# --------------------------------------------------------------------------- #


def test_valid_rs256_token_resolves_via_kid_and_returns_claims(
    verifier: ContractVerifier, primary_key: dict[str, str]
) -> None:
    claims = verifier.verify(_mint(primary_key["private_pem"], kid=PRIMARY_KID))
    assert claims["org_id"] == "org_acme"
    assert claims["roles"] == ["admin", "member"]
    assert claims["sub"] == "user_123"


def test_unknown_kid_triggers_jwks_refresh_then_verifies(
    verifier: ContractVerifier,
    source: JwksSource,
    primary_key: dict[str, str],
    rotated_key: dict[str, str],
) -> None:
    # Warm the cache against the primary key.
    verifier.verify(_mint(primary_key["private_pem"], kid=PRIMARY_KID))
    fetches_after_warm = source.fetch_count

    # IdP rotates: a new signing key is published under a new kid.
    source.publish(ROTATED_KID, rotated_key["public_pem"])
    token = _mint(rotated_key["private_pem"], kid=ROTATED_KID)

    claims = verifier.verify(token)  # cache miss on ROTATED_KID -> one refresh
    assert claims["org_id"] == "org_acme"
    assert source.fetch_count == fetches_after_warm + 1


def test_unknown_kid_absent_after_refresh_is_rejected(
    verifier: ContractVerifier,
    source: JwksSource,
    primary_key: dict[str, str],
    rotated_key: dict[str, str],
) -> None:
    verifier.verify(_mint(primary_key["private_pem"], kid=PRIMARY_KID))  # warm
    fetches_after_warm = source.fetch_count

    # Signed with a never-published kid: refresh is attempted exactly once.
    ghost = _mint(rotated_key["private_pem"], kid="ghost-kid")
    with pytest.raises(TokenError) as exc:
        verifier.verify(ghost)
    assert str(exc.value) == "unknown_kid"
    assert source.fetch_count == fetches_after_warm + 1


def test_expired_token_is_rejected(
    verifier: ContractVerifier, primary_key: dict[str, str]
) -> None:
    token = _mint(primary_key["private_pem"], kid=PRIMARY_KID, exp_offset=-10)
    with pytest.raises(TokenError) as exc:
        verifier.verify(token)
    assert str(exc.value) == "token_expired"


def test_tampered_signature_is_rejected(
    verifier: ContractVerifier, primary_key: dict[str, str]
) -> None:
    head, payload, sig = _mint(primary_key["private_pem"], kid=PRIMARY_KID).split(".")
    # Flip a real signature byte. (Mutating the trailing base64url char is a
    # no-op: its low bits are padding, so the decoded RSA signature is unchanged.)
    raw = bytearray(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
    raw[0] ^= 0x01
    bad_sig = _b64url(bytes(raw))
    with pytest.raises(TokenError) as exc:
        verifier.verify(f"{head}.{payload}.{bad_sig}")
    assert str(exc.value) == "bad_signature"


def test_hs256_confusion_attack_is_rejected(
    verifier: ContractVerifier, primary_key: dict[str, str]
) -> None:
    now = int(time.time())
    payload = {
        "sub": "user_123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 3600,
        "org_id": "org_acme",
        "roles": ["member"],
    }
    forged = _forge_hs256(payload, secret=primary_key["public_pem"], kid=PRIMARY_KID)
    with pytest.raises(TokenError) as exc:
        verifier.verify(forged)
    assert str(exc.value) == "bad_algorithm"


def test_alg_none_token_is_rejected(verifier: ContractVerifier) -> None:
    now = int(time.time())
    payload = {
        "sub": "user_123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 3600,
        "org_id": "org_acme",
        "roles": ["member"],
    }
    forged = jwt.encode(payload, None, algorithm="none", headers={"kid": PRIMARY_KID})
    with pytest.raises(TokenError) as exc:
        verifier.verify(forged)
    assert str(exc.value) == "bad_algorithm"


def test_missing_kid_is_rejected(
    verifier: ContractVerifier, primary_key: dict[str, str]
) -> None:
    token = _mint(primary_key["private_pem"], kid=None)
    with pytest.raises(TokenError) as exc:
        verifier.verify(token)
    assert str(exc.value) == "missing_kid"


def test_malformed_token_is_rejected(verifier: ContractVerifier) -> None:
    with pytest.raises(TokenError) as exc:
        verifier.verify("not-a-jwt")
    assert str(exc.value) == "malformed_token"
