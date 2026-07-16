"""Retros: list/create/get (legacy ``routes/retros.ts``).

snake_case wire -- deliberately unlike sprints; the legacy retros surface
predates the camelCase convention and the frontend placeholder reads it as-is.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import AuditLog
from ..auth import ParadigmClaims
from ..deps import get_audit, get_session, require_claims
from ..models import Retro

router = APIRouter()


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": message})


@router.get("/api/retros")
def list_retros(
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    retros = session.execute(select(Retro).order_by(Retro.id.asc())).scalars().all()
    return {"retros": [r.public_dict() for r in retros]}


@router.post("/api/retros", status_code=201)
def create_retro(
    body: Any = Body(None),
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> dict:
    data = body if isinstance(body, dict) else {}
    held_on = data.get("heldOn")
    if not isinstance(held_on, str) or not held_on.strip():
        raise _bad_request("heldOn is required")
    sprint_id = data.get("sprintId")
    summary = data.get("summary")
    retro = Retro(
        org_id=claims.org_id,
        # sprintId: numbers coerce with int(); anything non-numeric is null.
        sprint_id=(
            int(sprint_id)
            if isinstance(sprint_id, int | float) and not isinstance(sprint_id, bool)
            else None
        ),
        held_on=held_on,
        summary=summary if isinstance(summary, str) else None,
    )
    session.add(retro)
    session.flush()
    session.refresh(retro)  # populate the Identity id for the wire body
    audit.emit(
        action="retro.created",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="retro",
        resource_id=str(retro.id),
        session=session,
    )
    return {"id": retro.id}


@router.get("/api/retros/{retro_id}")
def get_retro(
    retro_id: str,
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    # Path id declared as str so a non-integer segment yields the legacy 400.
    try:
        parsed = int(retro_id)
    except ValueError as exc:
        raise _bad_request("bad id") from exc
    retro = session.get(Retro, parsed)
    if retro is None:
        raise HTTPException(status_code=404, detail={"error": "no such retro"})
    return retro.public_dict()  # the row itself, not wrapped (legacy shape)


__all__ = ["router"]
