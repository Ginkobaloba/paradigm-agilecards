"""Saved views — scoped to (org, caller sub). Contract §Saved views."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session, sessionmaker

from .. import repository as repo
from .. import wire
from ..auth import ParadigmClaims
from ..bus import OrgEventBus
from ..deps import require_claims
from .common import err, get_bus, get_db, mutation, reading, require_json_object

router = APIRouter()

_BAD_NAME = "name must be a non-empty string <= 80 chars"


def _valid_name(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed and len(trimmed) <= repo.MAX_VIEW_NAME:
            return trimmed
    return None


@router.get("/api/views")
def list_views(
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    with reading(factory, claims) as session:
        views = repo.list_views(session, claims.org_id, claims.sub)
        return {"views": [wire.saved_view(v) for v in views]}


@router.get("/api/views/{view_id}")
def get_view(
    view_id: int,
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    with reading(factory, claims) as session:
        view = repo.get_view(session, claims.org_id, claims.sub, view_id)
        if view is None:
            raise err(404, "no such view")
        return wire.saved_view(view)


@router.post("/api/views", status_code=201)
def create_view(
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    body = require_json_object(body)
    name = _valid_name(body.get("name"))
    if name is None:
        raise err(400, _BAD_NAME)
    with mutation(factory, claims, bus) as ctx:
        view = repo.create_view(ctx.session, claims.org_id, claims.sub, name, body.get("payload"))
        ctx.audit("view.create", resource_type="view", resource_id=str(view.id))
        response = wire.saved_view(view)
    return response


@router.patch("/api/views/{view_id}")
def update_view(
    view_id: int,
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    body = require_json_object(body)
    name: str | None = None
    if "name" in body:
        name = _valid_name(body.get("name"))
        if name is None:
            raise err(400, "invalid name")
    payload = body.get("payload", ...) if "payload" in body else ...
    with mutation(factory, claims, bus) as ctx:
        try:
            view = repo.update_view(
                ctx.session, claims.org_id, claims.sub, view_id, name=name, payload=payload
            )
        except repo.DomainValidationError as exc:
            raise err(400, "payload too large") from exc
        ctx.audit("view.update", resource_type="view", resource_id=str(view_id))
        response = wire.saved_view(view)
    return response


@router.delete("/api/views/{view_id}", status_code=204)
def delete_view(
    view_id: int,
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> None:
    with mutation(factory, claims, bus) as ctx:
        repo.delete_view(ctx.session, claims.org_id, claims.sub, view_id)
        ctx.audit("view.delete", resource_type="view", resource_id=str(view_id))
