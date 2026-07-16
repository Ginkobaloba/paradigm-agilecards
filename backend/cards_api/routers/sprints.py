"""Sprints: CRUD, archive filtering, sprint-card links (legacy ``routes/sprints.ts``).

camelCase wire, exact legacy error strings. ``startsAt``/``endsAt``/``archivedAt``
are TEXT end to end: legacy validated and compared them as ISO *strings*, so the
endsAt < startsAt check and the list ordering are plain string comparisons --
never parsed dates (see models.py ADR note).
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import AuditLog
from ..auth import ParadigmClaims
from ..deps import get_audit, get_session, require_claims
from ..models import SPRINT_STATUSES, Sprint, SprintCard

router = APIRouter()

_TRUTHY = {"1", "true", "yes"}


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": message})


def _no_such_sprint() -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "no such sprint"})


def _parse_id(raw: str) -> int:
    # Path ids are declared as str so a non-integer segment yields the legacy
    # 400 body instead of FastAPI's default validation error.
    try:
        return int(raw)
    except ValueError as exc:
        raise _bad_request("bad id") from exc


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


@router.get("/api/sprints")
def list_sprints(
    includeArchived: str | None = None,  # noqa: N803 - legacy query param name
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    query = select(Sprint)
    if (includeArchived or "").lower() not in _TRUTHY:
        query = query.where(Sprint.archived_at.is_(None))
    query = query.order_by(Sprint.starts_at.desc())
    sprints = session.execute(query).scalars().all()

    rollups = {
        sprint_id: (card_count, planned_sum)
        for sprint_id, card_count, planned_sum in session.execute(
            select(
                SprintCard.sprint_id,
                func.count(),
                func.coalesce(func.sum(SprintCard.planned_points), 0),
            ).group_by(SprintCard.sprint_id)
        )
    }
    summaries = []
    for sprint in sprints:
        card_count, planned_sum = rollups.get(sprint.id, (0, 0))
        summaries.append(
            {**sprint.public_dict(), "cardCount": card_count, "plannedPointsSum": int(planned_sum)}
        )
    return {"sprints": summaries}


@router.post("/api/sprints", status_code=201)
def create_sprint(
    body: Any = Body(None),
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> dict:
    data = body if isinstance(body, dict) else {}
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _bad_request("name is required")
    starts_at = data.get("startsAt")
    ends_at = data.get("endsAt")
    if (
        not isinstance(starts_at, str)
        or not starts_at.strip()
        or not isinstance(ends_at, str)
        or not ends_at.strip()
    ):
        raise _bad_request("startsAt and endsAt are required")
    if ends_at < starts_at:  # plain ISO-string comparison, legacy semantics
        raise _bad_request("endsAt cannot be before startsAt")
    goal = data.get("goal")
    status = data.get("status")
    sprint = Sprint(
        org_id=claims.org_id,
        name=name.strip(),
        starts_at=starts_at,
        ends_at=ends_at,
        goal=goal if isinstance(goal, str) else None,
        # Legacy quirk: an invalid status on create is not an error, it
        # silently defaults to "planning".
        status=status if status in SPRINT_STATUSES else "planning",
    )
    session.add(sprint)
    session.flush()
    session.refresh(sprint)  # populate Identity id + server-side created_at
    audit.emit(
        action="sprint.created",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="sprint",
        resource_id=str(sprint.id),
        session=session,
    )
    return {"sprint": sprint.public_dict()}


@router.get("/api/sprints/{sprint_id}")
def get_sprint(
    sprint_id: str,
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    sprint = session.get(Sprint, _parse_id(sprint_id))
    if sprint is None:
        raise _no_such_sprint()
    links = (
        session.execute(
            select(SprintCard)
            .where(SprintCard.sprint_id == sprint.id)
            .order_by(SprintCard.card_id.asc())
        )
        .scalars()
        .all()
    )
    return {"sprint": sprint.public_dict(), "cards": [link.public_dict() for link in links]}


@router.patch("/api/sprints/{sprint_id}")
def patch_sprint(
    sprint_id: str,
    body: Any = Body(None),
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> dict:
    sprint = session.get(Sprint, _parse_id(sprint_id))
    if sprint is None:  # existence before patch validation, matching cards.py
        raise _no_such_sprint()
    patch = body if isinstance(body, dict) else {}
    recognized: list[str] = []

    if "name" in patch:
        value = patch["name"]
        if not isinstance(value, str) or not value.strip():
            raise _bad_request("name must be a non-empty string")
        sprint.name = value.strip()
        recognized.append("name")
    # startsAt/endsAt: legacy only picks these up when they are non-empty
    # strings; other values are silently not recognized.
    if "startsAt" in patch and isinstance(patch["startsAt"], str) and patch["startsAt"]:
        sprint.starts_at = patch["startsAt"]
        recognized.append("startsAt")
    if "endsAt" in patch and isinstance(patch["endsAt"], str) and patch["endsAt"]:
        sprint.ends_at = patch["endsAt"]
        recognized.append("endsAt")
    # goal/archivedAt: string or null; anything else coerces to null (legacy).
    if "goal" in patch:
        sprint.goal = patch["goal"] if isinstance(patch["goal"], str) else None
        recognized.append("goal")
    if "archivedAt" in patch:
        sprint.archived_at = patch["archivedAt"] if isinstance(patch["archivedAt"], str) else None
        recognized.append("archivedAt")
    if "status" in patch:
        if patch["status"] not in SPRINT_STATUSES:
            raise _bad_request("invalid status")
        sprint.status = patch["status"]
        recognized.append("status")
    if "pointsTarget" in patch:
        value = patch["pointsTarget"]
        if value is None:
            sprint.points_target = None
        elif _is_number(value):
            sprint.points_target = max(0, math.floor(value))
        else:
            raise _bad_request("pointsTarget must be a number")
        recognized.append("pointsTarget")
    if "dollarTarget" in patch:
        value = patch["dollarTarget"]
        if value is None:
            sprint.dollar_target = None
        elif _is_number(value):
            sprint.dollar_target = float(max(0, value))
        else:
            raise _bad_request("dollarTarget must be a number")
        recognized.append("dollarTarget")
    if "reviewHoursTarget" in patch:
        value = patch["reviewHoursTarget"]
        if value is None:
            sprint.review_hours_target = None
        elif _is_number(value):
            sprint.review_hours_target = float(max(0, value))
        else:
            raise _bad_request("reviewHoursTarget must be a number")
        recognized.append("reviewHoursTarget")

    if not recognized:
        raise _bad_request("no recognized fields in patch")
    session.flush()
    session.refresh(sprint)
    audit.emit(
        action="sprint.updated",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="sprint",
        resource_id=str(sprint.id),
        detail={"fields": sorted(recognized)},
        session=session,
    )
    return {"sprint": sprint.public_dict()}


@router.post("/api/sprints/{sprint_id}/cards", status_code=204)
def add_sprint_card(
    sprint_id: str,
    body: Any = Body(None),
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> Response:
    sid = _parse_id(sprint_id)
    card_id = body.get("cardId") if isinstance(body, dict) else None
    if not isinstance(card_id, str) or not card_id:
        raise _bad_request("cardId is required")
    if session.get(Sprint, sid) is None:
        raise _no_such_sprint()
    planned = body.get("plannedPoints")
    planned_points = max(0, math.floor(planned)) if _is_number(planned) else None

    # Upsert (legacy INSERT OR REPLACE): re-adding a linked card updates points.
    link = session.get(SprintCard, {"org_id": claims.org_id, "sprint_id": sid, "card_id": card_id})
    if link is None:
        session.add(
            SprintCard(
                org_id=claims.org_id,
                sprint_id=sid,
                card_id=card_id,
                planned_points=planned_points,
            )
        )
    else:
        link.planned_points = planned_points
    session.flush()
    audit.emit(
        action="sprint.card_added",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="sprint",
        resource_id=str(sid),
        detail={"cardId": card_id},
        session=session,
    )
    return Response(status_code=204)


@router.delete("/api/sprints/{sprint_id}/cards/{card_id}", status_code=204)
def remove_sprint_card(
    sprint_id: str,
    card_id: str,
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> Response:
    sid = _parse_id(sprint_id)
    # Idempotent (legacy): deleting an absent link is still 204, never 404.
    link = session.get(SprintCard, {"org_id": claims.org_id, "sprint_id": sid, "card_id": card_id})
    if link is not None:
        session.delete(link)
        session.flush()
    audit.emit(
        action="sprint.card_removed",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="sprint",
        resource_id=str(sid),
        detail={"cardId": card_id},
        session=session,
    )
    return Response(status_code=204)


__all__ = ["router"]
