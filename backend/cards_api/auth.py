"""Paradigm JWT verification for the AgileCards FastAPI backend (AC-CARDS-003).

Verifies RS256 access tokens against the Paradigm IdP JWKS endpoint. No
symmetric algorithm is ever accepted -- ``algorithms=["RS256"]`` is a hard
allowlist, which is what makes the HS256-with-public-key confusion attack
impossible (AC-AUTH-003 / AC-SEC-003).

There is no Python ``@paradigm/auth`` SDK in v1; Python services verify
directly against JWKS. This module is the first consumer of the seed at
``paradigm-platform/docs/runbooks/python-jwt-verification.md`` and is written
for dependency injection so tests can supply a mock JWK client (no network).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import jwt
from jwt import PyJWKClient

# Algorithms the verifier will accept. RS256 only -- do NOT widen this.
_ALLOWED_ALGORITHMS = ["RS256"]
# Claims that must be present and validated on every token.
_REQUIRED_CLAIMS = ["exp", "iat", "nbf", "sub", "iss", "aud"]


@dataclass(frozen=True)
class ParadigmClaims:
    """The subset of verified claims the application authorizes against."""

    sub: str
    org_id: str
    roles: tuple[str, ...]
    raw: dict


class TokenError(Exception):
    """Raised when a token is missing, malformed, or fails verification.

    The string value is a stable, non-leaking reason code (e.g. ``token_expired``)
    suitable for returning in a 401 body.
    """


class SigningKey(Protocol):
    """The shape ``jwt.decode`` needs: an object exposing the verifying key."""

    key: object


class JWKClient(Protocol):
    """Minimal JWK-client contract. ``PyJWKClient`` satisfies it; so do mocks."""

    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


class TokenVerifier:
    """Verifies Paradigm RS256 access tokens against a JWKS.

    Args:
        issuer: expected ``iss`` claim.
        audience: expected ``aud`` claim (this service's registered audience).
        jwks_url: JWKS endpoint; used only when ``jwk_client`` is not supplied.
        jwk_client: an object resolving a signing key from a token. Injecting it
            keeps the verifier offline-testable (mock for tests, AC-CARDS-003).
        leeway: clock-skew tolerance in seconds for exp/nbf (keep small).
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        jwk_client: JWKClient | None = None,
        leeway: int = 0,
    ) -> None:
        if jwk_client is None:
            if not jwks_url:
                raise ValueError("TokenVerifier requires jwks_url or jwk_client")
            # PyJWKClient caches keys in memory and refetches on an unknown kid,
            # which is the JWKS cache behavior the platform expects
            # (AC-AUTH-011/012). Construction does no network I/O.
            jwk_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=600)
        self._jwk_client = jwk_client
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway

    def verify(self, token: str) -> ParadigmClaims:
        """Verify ``token`` and return its claims. Raises ``TokenError`` on any
        failure (signature, alg, iss, aud, exp, nbf, unknown kid, malformed)."""
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=_ALLOWED_ALGORITHMS,
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={
                    "require": _REQUIRED_CLAIMS,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_signature": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("token_expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenError("bad_audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenError("bad_issuer") from exc
        except jwt.PyJWTError as exc:  # signature failure, bad alg, malformed, unknown kid
            raise TokenError("invalid_token") from exc

        org_id = payload.get("org_id")
        if not org_id:
            raise TokenError("missing_org_id")

        return ParadigmClaims(
            sub=payload["sub"],
            org_id=org_id,
            roles=tuple(payload.get("roles", ())),
            raw=payload,
        )
