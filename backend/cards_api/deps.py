"""FastAPI auth dependencies (AC-CARDS-006 / AC-CARDS-007).

``require_claims`` turns the verifier into a 401-on-failure guard that every
authenticated endpoint depends on. ``require_roles`` /``require_any_role``
layer role-based authorization on top for privileged routes. Every denial is
recorded through the audit seam (ADR D4).

Note on the missing-token status code: FastAPI's ``HTTPBearer(auto_error=True)``
returns **403** when the Authorization header is absent. The chunk contract is
explicit that *no token -> 401*, so we use ``auto_error=False`` and raise 401
ourselves. This is a deliberate divergence from the seed runbook.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .audit import record_system_audit
from .auth import ParadigmClaims, TokenError, TokenVerifier
from .db import org_session

_bearer = HTTPBearer(auto_error=False)


def get_verifier(request: Request) -> TokenVerifier:
    return request.app.state.verifier


def require_claims(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    verifier: TokenVerifier = Depends(get_verifier),
) -> ParadigmClaims:
    if creds is None or not creds.credentials:
        record_system_audit(
            request.app.state.session_factory,
            action="auth.token_verify",
            outcome="missing_token",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verifier.verify(creds.credentials)
    except TokenError as exc:
        record_system_audit(
            request.app.state.session_factory,
            action="auth.token_verify",
            outcome=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _deny(request: Request, claims: ParadigmClaims, required: tuple[str, ...]) -> None:
    """Audit a role denial under the caller's org, then 403."""
    factory = request.app.state.session_factory
    if factory is not None:
        from .audit import record_audit

        try:
            with org_session(factory, claims.org_id) as session:
                record_audit(
                    session,
                    org_id=claims.org_id,
                    actor_sub=claims.sub,
                    action="auth.role_check",
                    outcome="denied",
                    details={"required": list(required), "held": list(claims.roles)},
                )
        except Exception:  # noqa: BLE001 - audit is best-effort on the deny path
            pass
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": "insufficient_role", "required": list(required)},
    )


def require_roles(*required: str) -> Callable[..., ParadigmClaims]:
    """Dependency factory: require the caller to hold ALL of ``required``."""

    def _dep(
        request: Request, claims: ParadigmClaims = Depends(require_claims)
    ) -> ParadigmClaims:
        if not set(required).issubset(set(claims.roles)):
            _deny(request, claims, required)
        return claims

    return _dep


def require_any_role(*required: str) -> Callable[..., ParadigmClaims]:
    """Dependency factory: require the caller to hold ANY of ``required``."""

    def _dep(
        request: Request, claims: ParadigmClaims = Depends(require_claims)
    ) -> ParadigmClaims:
        if not set(required).intersection(set(claims.roles)):
            _deny(request, claims, required)
        return claims

    return _dep
