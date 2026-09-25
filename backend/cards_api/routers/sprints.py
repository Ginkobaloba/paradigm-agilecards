"""Sprints (+ retros — same domain family). Contract §Sprints / §Retros."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session, sessionmaker

from .. import repository as repo
from .. import wire
from ..auth import ParadigmClaims
from ..bus import OrgEventBus
from ..deps import require_claims
from ..models import SPRINT_STATUSES
from .common import err, get_bus, get_db, mutation, reading, require_json_object

router = APIRouter()


@router.get("/api/sprints")
def list_sprints(
    includeArchived: str | None = None,  # noqa: N803 - legacy query param casing
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    include = (includeArchived or "").lower() in ("1", "true", "yes")
    with reading(factory, claims) as session:
        rows = repo.list_sprints(session, claims.org_id, include_archived=include)
        return {
            "sprints": [
                {**wire.sprint_shape(s), "cardCount": count, "plannedPointsSum": points}
                for s, count, points in rows
            ]
        }


@router.post("/api/sprints", status_code=201)
def create_sprint(
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    body = require_json_object(body)
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise err(400, "name is required")
    starts_at, ends_at = body.get("startsAt"), body.get("endsAt")
    if not isinstance(starts_at, str) or not starts_at or not isinstance(ends_at, str) or not ends_at:
        raise err(400, "startsAt and endsAt are required (ISO date)")
    if ends_at < starts_at:
        raise err(400, "endsAt cannot be before startsAt")
    status = body.get("status")
    if not isinstance(status, str) or status not in SPRINT_STATUSES:
        status = "planning"
    goal = body.get("goal")
    goal = goal if isinstance(goal, str) and goal.strip() else None

    with mutation(factory, claims, bus) as ctx:
        sprint = repo.create_sprint(
            ctx.session,
            claims.org_id,
            name=name.strip(),
            starts_at=starts_at,
            ends_at=ends_at,
            goal=goal,
            status=status,
        )
        ctx.audit("sprint.create", resource_type="sprint", resource_id=str(sprint.id))
        response = {"sprint": wire.sprint_shape(sprint)}
    return response


@router.get("/api/sprints/{sprint_id}")
def get_sprint(
    sprint_id: int,
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    with reading(factory, claims) as session:
        sprint = repo.get_sprint(session, claims.org_id, sprint_id)
        if sprint is None:
            raise err(404, "no such sprint")
        cards = repo.sprint_cards(session, claims.org_id, sprint_id)
        return {
            "sprint": wire.sprint_shape(sprint),
            "cards": [wire.sprint_card_link(c) for c in cards],
        }


@router.patch("/api/sprints/{sprint_id}")
def patch_sprint(
    sprint_id: int,
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    body = require_json_object(body)
    changes: dict[str, Any] = {}

    if "name" in body:
        name = body["name"]
        if isinstance(name, str):
            name = name.strip()
            if not name:
                raise err(400, "name cannot be empty")
            changes["name"] = name
    for wire_key, column in (("startsAt", "starts_at"), ("endsAt", "ends_at")):
        if wire_key in body and isinstance(body[wire_key], str) and body[wire_key]:
            changes[column] = body[wire_key]
    for wire_key, column in (("goal", "goal"), ("archivedAt", "archived_at")):
        if wire_key in body and (body[wire_key] is None or isinstance(body[wire_key], str)):
            changes[column] = body[wire_key]
    if "status" in body:
        status = body["status"]
        if isinstance(status, str):
            if status not in SPRINT_STATUSES:
                raise err(400, "status must be one of", valid=list(SPRINT_STATUSES))
            changes["status"] = status
    if "pointsTarget" in body:
        value = body["pointsTarget"]
        if value is None:
            changes["points_target"] = None
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            changes["points_target"] = max(0, int(value))
    for wire_key, column in (
        ("dollarTarget", "dollar_target"),
        ("reviewHoursTarget", "review_hours_target"),
    ):
        if wire_key in body:
            value = body[wire_key]
            if value is None:
                changes[column] = None
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                changes[column] = max(0.0, float(value))

    if not changes:
        raise err(400, "no recognized fields in body")

    with mutation(factory, claims, bus) as ctx:
        sprint = repo.get_sprint(ctx.session, claims.org_id, sprint_id)
        if sprint is None:
            raise err(404, "no such sprint")
        for column, value in changes.items():
            setattr(sprint, column, value)
        ctx.audit("sprint.update", resource_type="sprint", resource_id=str(sprint_id))
        response = {"sprint": wire.sprint_shape(sprint)}
    return response


@router.post("/api/sprints/{sprint_id}/cards", status_code=204)
def add_sprint_card(
    sprint_id: int,
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> None:
    body = require_json_object(body)
    card_id = body.get("cardId")
    if not isinstance(card_id, str) or not card_id:
        raise err(400, "cardId required")
    planned = body.get("plannedPoints")
    planned_points = (
        max(0, int(planned))
        if isinstance(planned, (int, float)) and not isinstance(planned, bool)
        else None
    )
    with mutation(factory, claims, bus) as ctx:
        if repo.get_sprint(ctx.session, claims.org_id, sprint_id) is None:
            raise err(404, "no such sprint")
        repo.upsert_sprint_card(ctx.session, claims.org_id, sprint_id, card_id, planned_points)
        ctx.audit(
            "sprint.add_card",
            resource_type="sprint",
            resource_id=str(sprint_id),
            details={"cardId": card_id},
        )


@router.delete("/api/sprints/{sprint_id}/cards/{card_id}", status_code=204)
def remove_sprint_card(
    sprint_id: int,
    card_id: str,
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> None:
    with mutation(factory, claims, bus) as ctx:
        repo.remove_sprint_card(ctx.session, claims.org_id, sprint_id, card_id)
        ctx.audit(
            "sprint.remove_card",
            resource_type="sprint",
            resource_id=str(sprint_id),
            details={"cardId": card_id},
        )


# --------------------------------------------------------------------------
# Retros (wire shape is raw snake_case — preserved legacy quirk)


@router.get("/api/retros")
def list_retros(
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    with reading(factory, claims) as session:
        retros = repo.list_retros(session, claims.org_id)
        return {"retros": [wire.retro_row(r) for r in retros]}


@router.post("/api/retros", status_code=201)
def create_retro(
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    body = require_json_object(body)
    held_on = body.get("heldOn")
    if not isinstance(held_on, str) or not held_on:
        raise err(400, "heldOn required (ISO date)")
    sprint_id = body.get("sprintId")
    sprint_id = (
        int(sprint_id)
        if isinstance(sprint_id, (int, float)) and not isinstance(sprint_id, bool)
        else None
    )
    summary = body.get("summary")
    summary = summary if isinstance(summary, str) else None
    with mutation(factory, claims, bus) as ctx:
        retro = repo.create_retro(
            ctx.session, claims.org_id, sprint_id=sprint_id, held_on=held_on, summary=summary
        )
        ctx.audit("retro.create", resource_type="retro", resource_id=str(retro.id))
        response = {"id": retro.id}
    return response


@router.get("/api/retros/{retro_id}")
def get_retro(
    retro_id: int,
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    with reading(factory, claims) as session:
        retro = repo.get_retro(session, claims.org_id, retro_id)
        if retro is None:
            raise err(404, "no such retro")
        return wire.retro_row(retro)
