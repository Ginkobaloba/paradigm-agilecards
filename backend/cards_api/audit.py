"""Audit logging seam (compliance seam #1, audit item S3).

Every security-relevant event -- auth failure, role denial, and every mutating
route -- flows through :class:`AuditLog`. Two sinks, always both:

1. A structured stdlib log record (``cards_api.audit`` logger), so events are
   visible in container/stdout logs even with no database configured.
2. A row in the append-only ``audit_events`` table when a database is present.
   Mutations pass their request session so the audit row commits atomically
   with the change it describes; pre-auth events (no verified org) use a
   system session and land with ``org_id NULL``.

Immutability and queryability are properties of the table (grants + trigger +
RLS in migration 0001), not of this module -- this module is only the emit hook.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import Database
from .models import AuditEvent

logger = logging.getLogger("cards_api.audit")

# Raw INSERT on purpose: both the ORM unit-of-work and SQLAlchemy Core add an
# implicit RETURNING (to fetch the identity PK), and Postgres checks RETURNING
# rows against the SELECT policy -- which deliberately hides org-NULL (pre-auth)
# rows even from their writer. Append-only means we never need the id back.
_INSERT_EVENT = text(
    "INSERT INTO audit_events"
    " (org_id, actor_sub, action, resource_type, resource_id, detail)"
    " VALUES (:org_id, :actor_sub, :action, :resource_type, :resource_id,"
    " CAST(:detail AS JSONB))"
)


class AuditLog:
    """Emit hook for audit events. ``db`` may be None (auth-only boot): events
    then go to the structured log alone rather than being dropped."""

    def __init__(self, db: Database | None) -> None:
        self._db = db

    def emit(
        self,
        *,
        action: str,
        org_id: str | None,
        actor_sub: str | None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        detail: dict | None = None,
        session: Session | None = None,
    ) -> None:
        """Record one event.

        Args:
            session: pass the request's org-bound session from mutating routes
                so the audit row is transactional with the mutation. When
                omitted, the event is written in its own system transaction
                (used for pre-auth failures, where no org context exists).
        """
        logger.info(
            "audit action=%s org_id=%s actor_sub=%s resource=%s/%s detail=%s",
            action,
            org_id,
            actor_sub,
            resource_type,
            resource_id,
            detail,
        )
        if session is not None:
            session.add(
                AuditEvent(
                    org_id=org_id,
                    actor_sub=actor_sub,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    detail=detail,
                )
            )
            return
        if self._db is None:
            return
        params = {
            "org_id": org_id,
            "actor_sub": actor_sub,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "detail": json.dumps(detail) if detail is not None else None,
        }
        try:
            if org_id is not None:
                # Org-attributed events (e.g. role denials) must satisfy the
                # INSERT policy's org check, so bind the org context.
                with self._db.org_session(org_id) as scoped:
                    scoped.execute(_INSERT_EVENT, params)
            else:
                with self._db.system_session() as scoped:
                    scoped.execute(_INSERT_EVENT, params)
        except Exception:  # noqa: BLE001 - auditing must never take down the request path
            logger.exception("audit_write_failed action=%s", action)
