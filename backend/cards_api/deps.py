"""FastAPI dependencies: auth guards, org-bound DB sessions, bus, audit.

``require_claims`` turns the verifier into a 401-on-failure guard that every
authenticated endpoint depends on. ``require_roles`` layers role-based
authorization on top. Both emit audit events on denial (compliance seam #1).

Note on the missing-token status code: FastAPI's ``HTTPBearer(auto_error=True)``
returns **403** when the Authorization header is absent. The chunk contract is
explicit that *no token -> 401*, so we use ``auto_error=False`` and raise 401
ourselves. This is a deliberate divergence from the seed runbook.

``get_session`` is the RLS choke point: it opens a transaction and binds the
*verified* token's ``org_id`` to ``app.current_org`` before any query runs
(see db.py). Routes never receive an unbound session.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .audit import AuditLog
from .auth import ParadigmClaims, TokenError, TokenVerifier
from .db import Database
from .events import EventBus

_bearer = HTTPBearer(auto_error=False)


def get_verifier(request: Request) -> TokenVerifier:
    return request.app.state.verifier


def get_database(request: Request) -> Database | None:
    return request.app.state.db


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


def get_audit(request: Request) -> AuditLog:
    return request.app.state.audit


def _verify_or_401(request: Request, token: str | None, verifier: TokenVerifier) -> ParadigmClaims:
    audit: AuditLog = request.app.state.audit
    if not token:
        audit.emit(
            action="auth.missing_token",
            org_id=None,
            actor_sub=None,
            detail={"path": request.url.path},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verifier.verify(token)
    except TokenError as exc:
        audit.emit(
            action="auth.token_rejected",
            org_id=None,
            actor_sub=None,
            detail={"path": request.url.path, "reason": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_claims(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    verifier: TokenVerifier = Depends(get_verifier),
) -> ParadigmClaims:
    token = creds.credentials if creds and creds.credentials else None
    return _verify_or_401(request, token, verifier)


def require_claims_header_or_query(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    verifier: TokenVerifier = Depends(get_verifier),
) -> ParadigmClaims:
    """Auth for the SSE route only. ``EventSource`` cannot set headers, so the
    legacy frontend passes ``?token=`` -- accepted here and nowhere else (the
    query-string exposure risk is documented in docs/security/DATA_PROTECTION.md).
    """
    token = creds.credentials if creds and creds.credentials else None
    if not token:
        token = request.query_params.get("token") or None
    return _verify_or_401(request, token, verifier)


def require_roles(*required: str) -> Callable[..., ParadigmClaims]:
    """Dependency factory: require the caller to hold all of ``required`` roles."""

    def _dep(
        request: Request, claims: ParadigmClaims = Depends(require_claims)
    ) -> ParadigmClaims:
        if not set(required).issubset(set(claims.roles)):
            audit: AuditLog = request.app.state.audit
            audit.emit(
                action="auth.role_denied",
                org_id=claims.org_id,
                actor_sub=claims.sub,
                detail={"path": request.url.path, "required": list(required)},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "insufficient_role", "required": list(required)},
            )
        return claims

    return _dep


def get_session(
    claims: ParadigmClaims = Depends(require_claims),
    db: Database | None = Depends(get_database),
    bus: EventBus = Depends(get_bus),
) -> Iterator[Session]:
    """A request-scoped transaction with RLS bound to the verified org.

    Commits when the route returns cleanly; rolls back when it raises. Routes
    that mutate should pass this same session to ``AuditLog.emit`` so the audit
    row is atomic with the mutation.

    SSE payloads queued during the request (``repo.queue_sse``) are published
    here, strictly after a successful commit -- a rolled-back write never
    reaches a subscriber. On rollback the exception propagates past the
    publish step, so the queue is simply dropped with the session.
    """
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "database_unavailable"},
        )
    with db.org_session(claims.org_id) as session:
        yield session
    from .repo import drain_sse  # local import: repo imports models, keep deps light

    for org_id, payload in drain_sse(session):
        bus.publish(org_id, payload)
