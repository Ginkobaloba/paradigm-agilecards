"""Saved board views: per-user CRUD (legacy ``routes/views.ts``).

Wire shapes and error strings are the legacy contract. A view belongs to a
(org, token subject) pair: RLS scopes every query to the verified org, and the
routes additionally filter ``owner_sub == claims.sub`` -- the per-user half the
org GUC cannot express. Foreign-subject views 404 rather than 403 so callers
cannot probe other users' view ids.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import AuditLog
from ..auth import ParadigmClaims
from ..deps import get_audit, get_session, require_claims
from ..models import SavedView

router = APIRouter()

# Legacy limits: name <= 80 chars after trim, payload <= 16 KiB serialized.
_NAME_MAX = 80
_PAYLOAD_MAX_BYTES = 16384

_NAME_ERROR = "name must be a non-empty string of at most 80 characters"
_PAYLOAD_ERROR = f"payload too large (max {_PAYLOAD_MAX_BYTES} bytes)"
_DUPLICATE_ERROR = "a view with that name already exists"


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": message})


def _no_such_view() -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "no such view"})


def _parse_id(raw: str) -> int:
    # Path ids are declared as str so a non-integer segment yields the legacy
    # 400 body instead of FastAPI's default validation error.
    try:
        return int(raw)
    except ValueError as exc:
        raise _bad_request("bad id") from exc


def _valid_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed or len(trimmed) > _NAME_MAX:
        return None
    return trimmed


def _payload_fits(value: Any) -> bool:
    return len(json.dumps(value).encode("utf-8")) <= _PAYLOAD_MAX_BYTES


def _get_own_view(session: Session, claims: ParadigmClaims, view_id: int) -> SavedView | None:
    return session.execute(
        select(SavedView).where(SavedView.id == view_id, SavedView.owner_sub == claims.sub)
    ).scalar_one_or_none()


@router.get("/api/views")
def list_views(
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    views = (
        session.execute(
            select(SavedView)
            .where(SavedView.owner_sub == claims.sub)
            .order_by(SavedView.name.asc())
        )
        .scalars()
        .all()
    )
    return {"views": [v.public_dict() for v in views]}


@router.post("/api/views", status_code=201)
def create_view(
    body: Any = Body(None),
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> dict:
    name = _valid_name(body.get("name") if isinstance(body, dict) else None)
    if name is None:
        raise _bad_request(_NAME_ERROR)
    payload = body.get("payload")
    if not _payload_fits(payload):
        raise _bad_request(_PAYLOAD_ERROR)

    view = SavedView(org_id=claims.org_id, owner_sub=claims.sub, name=name, payload=payload)
    session.add(view)
    try:
        # uq_saved_views_owner_name (org, sub, name) is the source of truth for
        # duplicates; a pre-check would still race.
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail={"error": _DUPLICATE_ERROR}) from exc
    session.refresh(view)
    audit.emit(
        action="view.created",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="saved_view",
        resource_id=str(view.id),
        session=session,
    )
    return view.public_dict()


@router.get("/api/views/{view_id}")
def get_view(
    view_id: str,
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    view = _get_own_view(session, claims, _parse_id(view_id))
    if view is None:
        raise _no_such_view()
    return view.public_dict()


@router.patch("/api/views/{view_id}")
def patch_view(
    view_id: str,
    body: Any = Body(None),
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> dict:
    view = _get_own_view(session, claims, _parse_id(view_id))
    if view is None:  # existence before patch validation, matching cards.py
        raise _no_such_view()
    patch = body if isinstance(body, dict) else {}
    if "name" not in patch and "payload" not in patch:
        raise _bad_request("empty patch")
    if "name" in patch:
        name = _valid_name(patch["name"])
        if name is None:
            raise _bad_request(_NAME_ERROR)
        view.name = name
    if "payload" in patch:
        if not _payload_fits(patch["payload"]):
            raise _bad_request(_PAYLOAD_ERROR)
        view.payload = patch["payload"]
    try:
        session.flush()
    except IntegrityError as exc:  # rename onto an existing (org, sub, name)
        raise HTTPException(status_code=409, detail={"error": _DUPLICATE_ERROR}) from exc
    session.refresh(view)  # pick up the onupdate updated_at for the wire shape
    audit.emit(
        action="view.updated",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="saved_view",
        resource_id=str(view.id),
        detail={"fields": sorted(k for k in ("name", "payload") if k in patch)},
        session=session,
    )
    return view.public_dict()


@router.delete("/api/views/{view_id}", status_code=204)
def delete_view(
    view_id: str,
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> Response:
    view = _get_own_view(session, claims, _parse_id(view_id))
    if view is None:
        raise _no_such_view()
    resource_id = str(view.id)
    session.delete(view)
    session.flush()
    audit.emit(
        action="view.deleted",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="saved_view",
        resource_id=resource_id,
        session=session,
    )
    return Response(status_code=204)


__all__ = ["router"]
