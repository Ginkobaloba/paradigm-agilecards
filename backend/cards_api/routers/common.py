"""Shared router plumbing: error helper, app-state accessors, the
mutation-context pattern (transaction + audit + after-commit SSE publish),
and wire-level validation with legacy-exact error strings."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from ..audit import record_audit
from ..auth import ParadigmClaims
from ..bus import BoardEvent, OrgEventBus
from ..db import org_session
from ..models import STATUS_IDS
from ..repository import ConflictError, DomainValidationError, NotFoundError


def err(status_code: int, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": message, **extra})


def get_bus(request: Request) -> OrgEventBus:
    return request.app.state.bus


def get_db(request: Request) -> sessionmaker[Session]:
    factory = request.app.state.session_factory
    if factory is None:
        raise err(503, "database_unconfigured")
    return factory


class MutationContext:
    """Collects SSE events during a transaction; the route publishes them
    only after the commit succeeds (no ghost events on rollback)."""

    def __init__(self, session: Session, claims: ParadigmClaims) -> None:
        self.session = session
        self.claims = claims
        self.pending: list[BoardEvent] = []

    def emit(self, event: BoardEvent) -> None:
        self.pending.append(event)

    def audit(
        self,
        action: str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        outcome: str = "ok",
        details: Any = None,
    ) -> None:
        record_audit(
            self.session,
            org_id=self.claims.org_id,
            actor_sub=self.claims.sub,
            action=action,
            outcome=outcome,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
        )


@contextmanager
def mutation(
    factory: sessionmaker[Session], claims: ParadigmClaims, bus: OrgEventBus
) -> Iterator[MutationContext]:
    """Org-bound transaction that maps domain errors to contract HTTP errors
    and flushes collected SSE events after a successful commit."""
    try:
        with org_session(factory, claims.org_id) as session:
            ctx = MutationContext(session, claims)
            yield ctx
    except NotFoundError as exc:
        raise err(404, str(exc)) from exc
    except ConflictError as exc:
        raise err(409, str(exc)) from exc
    except DomainValidationError as exc:
        raise err(400, str(exc)) from exc
    else:
        for event in ctx.pending:
            bus.publish(claims.org_id, event)


@contextmanager
def reading(
    factory: sessionmaker[Session], claims: ParadigmClaims
) -> Iterator[Session]:
    """Read-only org-bound transaction with the same error mapping."""
    try:
        with org_session(factory, claims.org_id) as session:
            yield session
    except NotFoundError as exc:
        raise err(404, str(exc)) from exc
    except DomainValidationError as exc:
        raise err(400, str(exc)) from exc


def require_status(value: Any) -> str:
    if not isinstance(value, str) or value not in STATUS_IDS:
        raise HTTPException(
            status_code=400,
            detail={"error": "status must be one of", "valid": list(STATUS_IDS)},
        )
    return value


def require_json_object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise err(400, "body must be a JSON object")
    return body


_PATCHABLE_STAKES = ("low", "medium", "high")


def validate_frontmatter_patch(body: Any) -> dict[str, Any]:
    """The legacy whitelist, error strings preserved (contract §Cards)."""
    body = require_json_object(body)
    if not body:
        raise err(400, "empty patch")
    patch: dict[str, Any] = {}
    for key, value in body.items():
        if key == "stakes":
            if value is not None and value not in _PATCHABLE_STAKES:
                raise err(
                    400,
                    f"stakes must be one of low|medium|high|null, got {json.dumps(value)}",
                )
            patch[key] = value
        elif key == "cost_cap_usd":
            ok = value is None or (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value > 0
            )
            if not ok:
                raise err(
                    400,
                    f"cost_cap_usd must be a positive number or null, got {json.dumps(value)}",
                )
            patch[key] = value
        elif key == "title":
            if not isinstance(value, str) or not value.strip():
                raise err(
                    400, f"title must be a non-empty string, got {json.dumps(value)}"
                )
            patch[key] = value.strip()
        elif key == "points":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 6:
                raise err(400, f"points must be an integer 1..6, got {json.dumps(value)}")
            patch[key] = value
        elif key == "ready":
            if value is not None and not isinstance(value, bool):
                raise err(
                    400, f"ready must be a boolean or null, got {json.dumps(value)}"
                )
            patch[key] = value
        else:
            raise err(400, f"field not patchable: {key}")
    return patch
