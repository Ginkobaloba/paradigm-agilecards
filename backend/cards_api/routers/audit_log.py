"""Queryable side of the audit seam: admins read their own org's trail.
The write side lives in ``audit.py``; immutability is DB-enforced."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..auth import ParadigmClaims
from ..deps import require_roles
from ..models import AuditEventRow
from ..wire import _iso
from .common import err, get_db, reading

router = APIRouter()


@router.get("/api/audit")
def list_audit_events(
    limit: int | None = None,
    since: str | None = None,
    claims: ParadigmClaims = Depends(require_roles("admin")),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    since_ts: datetime | None = None
    if since:
        try:
            since_ts = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError as exc:
            raise err(400, "invalid since") from exc
        if since_ts.tzinfo is None:
            since_ts = since_ts.replace(tzinfo=timezone.utc)
    capped = max(1, min(limit or 200, 1000))
    with reading(factory, claims) as session:
        stmt = (
            select(AuditEventRow)
            .where(AuditEventRow.org_id == claims.org_id)
            .order_by(AuditEventRow.id.desc())
            .limit(capped)
        )
        if since_ts is not None:
            stmt = stmt.where(AuditEventRow.at > since_ts)
        rows = list(session.scalars(stmt))
        return {
            "events": [
                {
                    "id": row.id,
                    "at": _iso(row.at),
                    "actorSub": row.actor_sub,
                    "action": row.action,
                    "resourceType": row.resource_type,
                    "resourceId": row.resource_id,
                    "outcome": row.outcome,
                    "details": row.details,
                }
                for row in rows
            ]
        }
