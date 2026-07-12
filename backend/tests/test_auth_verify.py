"""AC-CARDS-003 -- direct JWKS verification (PyJWT + cryptography).

The verifier resolves the signing key from a JWKS by ``kid`` and validates an
RS256 signature plus iss/aud/exp/nbf. Symmetric algorithms are rejected outright
so the HS256-with-public-key confusion attack is impossible.
"""

from __future__ import annotations

import pytest
from cards_api.auth import ParadigmClaims, TokenError, TokenVerifier

from conftest import AUDIENCE, ISSUER, KID, ORG_A


@pytest.fixture
def verifier(jwk_client) -> TokenVerifier:
    return TokenVerifier(issuer=ISSUER, audience=AUDIENCE, jwk_client=jwk_client)


def test_valid_token_returns_claims(verifier, make_token) -> None:
    claims = verifier.verify(make_token(org_id=ORG_A, roles=["admin", "member"]))
    assert isinstance(claims, ParadigmClaims)
    assert claims.org_id == ORG_A
    assert claims.roles == ("admin", "member")
    assert claims.sub == "user_123"


def test_tampered_signature_is_rejected(verifier, make_token) -> None:
    # Splice a valid header.payload onto a signature minted for *different*
    # claims. The signature no longer matches the signed content -> reject.
    head_a, payload_a, _ = make_token(sub="user_a").split(".")
    _, _, sig_b = make_token(sub="user_b").split(".")
    with pytest.raises(TokenError):
        verifier.verify(f"{head_a}.{payload_a}.{sig_b}")


def test_hs256_token_is_rejected(verifier, make_token) -> None:
    # The verifier's RS256 allowlist must reject *any* HS256 token -- this is
    # the defense against the symmetric-confusion attack (AC-AUTH-003).
    forged = make_token(alg="HS256", key="shared-secret-the-attacker-fully-controls", kid=KID)
    with pytest.raises(TokenError):
        verifier.verify(forged)


def test_expired_token_is_rejected(verifier, make_token) -> None:
    with pytest.raises(TokenError) as exc:
        verifier.verify(make_token(exp_offset=-10))
    assert str(exc.value) == "token_expired"


def test_not_yet_valid_token_is_rejected(verifier, make_token) -> None:
    with pytest.raises(TokenError):
        verifier.verify(make_token(nbf_offset=3600))


def test_wrong_issuer_is_rejected(verifier, make_token) -> None:
    with pytest.raises(TokenError) as exc:
        verifier.verify(make_token(issuer="https://evil.example.com"))
    assert str(exc.value) == "bad_issuer"


def test_wrong_audience_is_rejected(verifier, make_token) -> None:
    with pytest.raises(TokenError) as exc:
        verifier.verify(make_token(audience="some-other-service"))
    assert str(exc.value) == "bad_audience"


def test_unknown_kid_is_rejected(verifier, make_token) -> None:
    with pytest.raises(TokenError):
        verifier.verify(make_token(kid="not-a-known-kid"))


def test_missing_org_id_is_rejected(verifier, make_token) -> None:
    with pytest.raises(TokenError) as exc:
        verifier.verify(make_token(drop_claims=("org_id",)))
    assert str(exc.value) == "missing_org_id"


def test_malformed_token_is_rejected(verifier) -> None:
    with pytest.raises(TokenError):
        verifier.verify("not-a-jwt")
