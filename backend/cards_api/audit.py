"""Audit seam (ADR D4): every security-relevant action leaves two traces —
an append-only row in ``audit_events`` (same transaction as the mutation, so
the trail and the change commit or roll back together) and a structured JSON
log line on stdout for shipper pickup.

Immutability is enforced by the database, not by convention: the runtime
role has INSERT+SELECT only (proven in tests/pg/test_rls_enforcement.py).

Auth failures happen before any org is verified; they are recorded under the
``__system__`` sentinel org via their own short transaction.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .db import SYSTEM_ORG, bind_org
from .models import AuditEventRow

logger = logging.getLogger("cards_api.audit")


def configure_logging() -> None:
    """Structured (JSON-lines) logging for the app's own loggers. Idempotent."""
    root = logging.getLogger("cards_api")
    if any(isinstance(h, _JsonHandler) for h in root.handlers):
        return
    handler = _JsonHandler()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class _JsonHandler(logging.StreamHandler):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatter.formatTime(record) if self.formatter else record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "audit", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


def record_audit(
    session: Session,
    *,
    org_id: str,
    actor_sub: str | None,
    action: str,
    outcome: str = "ok",
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: Any = None,
) -> None:
    """Append an audit row inside the caller's transaction + emit a log line."""
    session.add(
        AuditEventRow(
            org_id=org_id,
            actor_sub=actor_sub,
            action=action,
            outcome=outcome,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
        )
    )
    logger.info(
        "audit",
        extra={
            "audit": {
                "org_id": org_id,
                "actor_sub": actor_sub,
                "action": action,
                "outcome": outcome,
                "resource_type": resource_type,
                "resource_id": resource_id,
            }
        },
    )


def record_system_audit(
    session_factory: sessionmaker[Session] | None,
    *,
    action: str,
    outcome: str,
    details: Any = None,
) -> None:
    """Audit an event with no verified org (auth failures). Best-effort: an
    unreachable database must not turn a 401 into a 500."""
    logger.info(
        "audit",
        extra={"audit": {"org_id": SYSTEM_ORG, "action": action, "outcome": outcome}},
    )
    if session_factory is None:
        return
    try:
        with session_factory() as session, session.begin():
            bind_org(session, SYSTEM_ORG)
            session.add(
                AuditEventRow(
                    org_id=SYSTEM_ORG, action=action, outcome=outcome, details=details
                )
            )
    except Exception:  # noqa: BLE001 - deliberately best-effort
        logger.warning("audit_write_failed", extra={"audit": {"action": action}})
