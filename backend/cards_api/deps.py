"""FastAPI auth dependencies (AC-CARDS-006 / AC-CARDS-007).

``require_claims`` turns the verifier into a 401-on-failure guard that every
authenticated endpoint depends on. ``require_roles`` layers role-based
authorization on top for mutating routes.

Note on the missing-token status code: FastAPI's ``HTTPBearer(auto_error=True)``
returns **403** when the Authorization header is absent. The chunk contract is
explicit that *no token -> 401*, so we use ``auto_error=False`` and raise 401
ourselves. This is a deliberate divergence from the seed runbook.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth import ParadigmClaims, TokenError, TokenVerifier
from .store import CardStore

_bearer = HTTPBearer(auto_error=False)


def get_verifier(request: Request) -> TokenVerifier:
    return request.app.state.verifier


def get_store(request: Request) -> CardStore:
    return request.app.state.store


def require_claims(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    verifier: TokenVerifier = Depends(get_verifier),
) -> ParadigmClaims:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verifier.verify(creds.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_roles(*required: str) -> Callable[..., ParadigmClaims]:
    """Dependency factory: require the caller to hold all of ``required`` roles."""

    def _dep(claims: ParadigmClaims = Depends(require_claims)) -> ParadigmClaims:
        if not set(required).issubset(set(claims.roles)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "insufficient_role", "required": list(required)},
            )
        return claims

    return _dep
