"""Cards: CRUD, move, frontmatter patch, ranks, per-card event log."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session, sessionmaker

from .. import repository as repo
from .. import wire
from ..auth import ParadigmClaims
from ..bus import OrgEventBus
from ..deps import require_claims, require_roles
from ..models import CardEventRow
from .common import (
    err,
    get_bus,
    get_db,
    mutation,
    reading,
    require_json_object,
    require_status,
    validate_frontmatter_patch,
)

router = APIRouter()


def _card_event_sse(card_id: str, events: list[CardEventRow]) -> list[dict[str, Any]]:
    return [
        {"type": "card-event-added", "cardId": card_id, "event": wire.card_event(e)}
        for e in events
    ]


@router.get("/api/cards")
def list_cards(
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    with reading(factory, claims) as session:
        cards = repo.list_cards(session, claims.org_id)
        return {"cards": [wire.card_summary(c) for c in cards]}


@router.get("/api/cards/{card_id}")
def get_card(
    card_id: str,
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    with reading(factory, claims) as session:
        card = repo.get_card(session, claims.org_id, card_id)
        if card is None:
            # 404 (not 403) so a caller cannot probe another org's card ids.
            raise err(404, "no such card")
        return wire.card_detail(card)


@router.post("/api/cards", status_code=201)
def create_card(
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_roles("admin")),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    body = require_json_object(body)
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise err(400, "title must be a non-empty string")
    status = require_status(body.get("status")) if "status" in body else "backlog"
    frontmatter = body.get("frontmatter") or {}
    if not isinstance(frontmatter, dict):
        raise err(400, "frontmatter must be an object")
    card_body = body.get("body") or ""
    if not isinstance(card_body, str):
        raise err(400, "body must be a string")

    raw_id = frontmatter.get("id")
    card_id = (
        raw_id
        if isinstance(raw_id, str) and raw_id.strip()
        else _slugify(title.strip())
    )
    frontmatter = {**frontmatter, "title": title.strip()}

    with mutation(factory, claims, bus) as ctx:
        # org_id is taken from the verified token, never from the request body.
        card, events = repo.create_card(
            ctx.session,
            claims.org_id,
            card_id=card_id,
            file=f"{card_id}.md",
            status=status,
            frontmatter=frontmatter,
            body=card_body,
        )
        ctx.audit("card.create", resource_type="card", resource_id=card.id)
        ctx.emit({"type": "card-added", "cardId": card.id, "status": card.status})
        for event in _card_event_sse(card.id, events):
            ctx.emit(event)
        response = wire.card_detail(card)
    return response


@router.post("/api/cards/{card_id}/move")
def move_card(
    card_id: str,
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    status = require_status(require_json_object(body).get("status"))
    with mutation(factory, claims, bus) as ctx:
        card, rank, events = repo.move_card(ctx.session, claims.org_id, card_id, status)
        ctx.audit(
            "card.move",
            resource_type="card",
            resource_id=card.id,
            details={"to": status},
        )
        ctx.emit(
            {"type": "card-state-changed", "cardId": card.id, "status": card.status}
        )
        for event in _card_event_sse(card.id, events):
            ctx.emit(event)
        response = {"id": card.id, "file": card.file, "status": card.status, "rank": rank}
    return response


@router.patch("/api/cards/{card_id}/frontmatter")
def patch_frontmatter(
    card_id: str,
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    patch = validate_frontmatter_patch(body)
    with mutation(factory, claims, bus) as ctx:
        card, events = repo.patch_frontmatter(ctx.session, claims.org_id, card_id, patch)
        ctx.audit(
            "card.frontmatter_patch",
            resource_type="card",
            resource_id=card.id,
            details={"fields": sorted(patch)},
        )
        ctx.emit({"type": "card-updated", "cardId": card.id, "status": card.status})
        for event in _card_event_sse(card.id, events):
            ctx.emit(event)
        response = wire.card_summary(card)
    return response


@router.get("/api/ranks")
def list_ranks(
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    with reading(factory, claims) as session:
        ranks = repo.list_ranks(session, claims.org_id)
        return {"ranks": [wire.rank_row(r) for r in ranks]}


@router.post("/api/cards/{card_id}/rank")
def set_rank(
    card_id: str,
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    body = require_json_object(body)
    status = require_status(body.get("status"))

    def _neighbor(key: str) -> str | None:
        value = body.get(key)
        return value if isinstance(value, str) and value else None

    with mutation(factory, claims, bus) as ctx:
        rank = repo.set_rank_between(
            ctx.session,
            claims.org_id,
            card_id,
            status,
            _neighbor("prevId"),
            _neighbor("nextId"),
        )
        ctx.audit("card.rank", resource_type="card", resource_id=card_id)
        response = {"cardId": card_id, "status": status, "rank": rank}
    return response


@router.get("/api/cards/{card_id}/events")
def list_card_events(
    card_id: str,
    limit: int | None = None,
    since: str | None = None,
    claims: ParadigmClaims = Depends(require_claims),
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
    with reading(factory, claims) as session:
        events = repo.list_card_events(
            session, claims.org_id, card_id, limit=limit or 500, since=since_ts
        )
        return {"events": [wire.card_event(e) for e in events]}


def _slugify(title: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in title]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "card"
