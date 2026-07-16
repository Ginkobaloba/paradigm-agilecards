"""Audit trail query surface (compliance seam #1: queryable).

Admin-only. RLS org-scopes the rows like any tenant table; pre-auth events
(org_id NULL) are operator-only by design and never appear here.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import ParadigmClaims
from ..deps import get_session, require_roles
from ..models import AuditEvent

router = APIRouter()


@router.get("/api/audit")
def list_audit_events(
    limit: int = 200,
    action: str | None = None,
    since: str | None = None,
    claims: ParadigmClaims = Depends(require_roles("admin")),
    session: Session = Depends(get_session),
) -> dict:
    capped = max(1, min(limit, 1000))
    query = select(AuditEvent)
    if action:
        query = query.where(AuditEvent.action == action)
    if since:
        try:
            since_ts = datetime.fromisoformat(since)
        except ValueError:
            since_ts = None
        if since_ts is not None:
            query = query.where(AuditEvent.ts > since_ts)
    query = query.order_by(AuditEvent.id.desc()).limit(capped)
    events = session.execute(query).scalars().all()
    return {"events": [e.public_dict() for e in events]}
