"""Cards: list/get/create, frontmatter patch, move, rank, timeline events.

Wire shapes, validation rules, and error strings are a faithful port of legacy
``routes/cards.ts`` / ``routes/ranks.ts`` / ``routes/events.ts``; the parity
spec is the contract. Bodies are parsed by hand (not Pydantic models) because
the legacy 400 messages are exact strings the frontend surfaces to users.

One deliberate divergence, documented: ``POST /api/cards/:id/rank`` returns 404
for an unknown card. Legacy accepted ranks for cards "not yet on disk" (a
chokidar-era artifact); with transactional card creation that case is gone and
``card_rank``'s FK enforces referential integrity instead.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import AuditLog
from ..auth import ParadigmClaims
from ..deps import get_audit, get_session, require_claims, require_roles
from ..models import STATUS_IDS, Card, CardEvent
from ..repo import add_card_event, append_rank, get_card, queue_sse, set_rank_between

router = APIRouter()

_STATUS_ORDER = {sid: i for i, sid in enumerate(STATUS_IDS)}

# PATCH /frontmatter whitelist (legacy validateFrontmatterPatch). Key -> validator
# returning (ok, coerced_value); None value means "delete the field".
_STAKES = ("low", "medium", "high")


def _bad_request(message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": message, **extra})


def _no_such_card() -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "no such card"})


@router.get("/api/cards")
def list_cards(
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    cards = session.execute(select(Card)).scalars().all()
    ordered = sorted(cards, key=lambda c: (_STATUS_ORDER.get(c.status, 99), c.id))
    return {"cards": [c.summary_dict() for c in ordered]}


@router.get("/api/cards/{card_id}")
def get_card_detail(
    card_id: str,
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    if not card_id.strip():
        raise _bad_request("missing id")
    card = get_card(session, card_id)
    if card is None:
        # 404 (not 403) so a caller cannot probe another org's card ids; RLS
        # makes foreign cards literally absent from this session's view.
        raise _no_such_card()
    return card.detail_dict()


@router.post("/api/cards", status_code=201)
def create_card(
    body: dict = Body(...),
    claims: ParadigmClaims = Depends(require_roles("admin")),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> dict:
    """Admin card creation (K11 route; not part of the board frontend contract).
    org_id is taken from the verified token, never from the request body."""
    title = body.get("title") if isinstance(body, dict) else None
    if not isinstance(title, str) or not title.strip():
        raise _bad_request("title must be a non-empty string")
    card = Card(
        org_id=claims.org_id,
        id=uuid.uuid4().hex,
        status="backlog",
        frontmatter={"title": title.strip()},
        body="",
    )
    session.add(card)
    session.flush()
    session.refresh(card)
    append_rank(session, org_id=claims.org_id, card_id=card.id, status=card.status)
    # Every card's timeline starts somewhere (legacy `discovered` backfill).
    add_card_event(session, org_id=claims.org_id, card_id=card.id, type_="discovered", details={})
    queue_sse(
        session,
        claims.org_id,
        {"type": "card-added", "cardId": card.id, "status": card.status},
    )
    audit.emit(
        action="card.created",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="card",
        resource_id=card.id,
        session=session,
    )
    return {**card.summary_dict(), "org_id": card.org_id}


@router.patch("/api/cards/{card_id}/frontmatter")
def patch_frontmatter(
    card_id: str,
    patch: Any = Body(None),
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> dict:
    card = get_card(session, card_id)
    if card is None:  # legacy checks existence before validating the patch
        raise _no_such_card()
    if not isinstance(patch, dict):
        raise _bad_request("patch must be an object")
    if not patch:
        raise _bad_request("empty patch")

    updates: dict[str, Any] = {}
    deletions: list[str] = []
    for key, value in patch.items():
        if key == "stakes":
            if value is None:
                deletions.append(key)
            elif isinstance(value, str) and value in _STAKES:
                updates[key] = value
            else:
                raise _bad_request("stakes must be one of low, medium, high (or null)")
        elif key == "cost_cap_usd":
            if value is None:
                deletions.append(key)
            elif isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
                updates[key] = value
            else:
                raise _bad_request("cost_cap_usd must be a positive number (or null)")
        elif key == "title":
            if isinstance(value, str) and value.strip():
                updates[key] = value.strip()
            else:
                raise _bad_request("title must be a non-empty string")
        elif key == "points":
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 6:
                updates[key] = value
            else:
                raise _bad_request("points must be an integer tier 1-6")
        elif key == "ready":
            if value is None:
                deletions.append(key)
            elif isinstance(value, bool):
                updates[key] = value
            else:
                raise _bad_request("ready must be a boolean (or null)")
        else:
            raise _bad_request(f"field not patchable: {key}")

    frontmatter = dict(card.frontmatter or {})
    frontmatter.update(updates)
    for key in deletions:
        frontmatter.pop(key, None)
    card.frontmatter = frontmatter
    session.flush()
    session.refresh(card)

    queue_sse(
        session,
        claims.org_id,
        {"type": "card-updated", "cardId": card.id, "status": card.status},
    )
    audit.emit(
        action="card.frontmatter_patched",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="card",
        resource_id=card.id,
        detail={"fields": sorted(patch.keys())},
        session=session,
    )
    return card.summary_dict()


@router.post("/api/cards/{card_id}/move")
def move_card(
    card_id: str,
    body: Any = Body(None),
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
    audit: AuditLog = Depends(get_audit),
) -> dict:
    status_value = body.get("status") if isinstance(body, dict) else None
    if status_value not in STATUS_IDS:
        raise _bad_request("status must be one of", valid=list(STATUS_IDS))
    card = get_card(session, card_id)
    if card is None:
        # Legacy surfaced any move failure as 409; keep the status, use the
        # standard message so the user sees something meaningful.
        raise HTTPException(status_code=409, detail={"error": "no such card"})

    previous = card.status
    if status_value != previous:
        card.status = status_value
        session.flush()
        session.refresh(card)
        add_card_event(
            session,
            org_id=claims.org_id,
            card_id=card.id,
            type_="status_changed",
            details={"from": previous, "to": status_value},
        )
        queue_sse(
            session,
            claims.org_id,
            {"type": "card-state-changed", "cardId": card.id, "status": card.status},
        )
        audit.emit(
            action="card.moved",
            org_id=claims.org_id,
            actor_sub=claims.sub,
            resource_type="card",
            resource_id=card.id,
            detail={"from": previous, "to": status_value},
            session=session,
        )
    # Legacy appends a fresh bottom-of-column rank even on a same-status no-op.
    rank = append_rank(session, org_id=claims.org_id, card_id=card.id, status=card.status)
    return {"id": card.id, "file": card.file, "status": card.status, "rank": rank}


@router.post("/api/cards/{card_id}/rank")
def set_rank(
    card_id: str,
    body: Any = Body(None),
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    if not card_id.strip():
        raise _bad_request("missing id")
    status_value = body.get("status") if isinstance(body, dict) else None
    if status_value not in STATUS_IDS:
        raise _bad_request("status must be one of", valid=list(STATUS_IDS))
    prev_id = body.get("prevId") if isinstance(body, dict) else None
    next_id = body.get("nextId") if isinstance(body, dict) else None
    prev_id = prev_id if isinstance(prev_id, str) and prev_id else None
    next_id = next_id if isinstance(next_id, str) and next_id else None

    if get_card(session, card_id) is None:  # documented divergence: FK-backed 404
        raise _no_such_card()
    rank = set_rank_between(
        session,
        org_id=claims.org_id,
        card_id=card_id,
        status=status_value,
        prev_id=prev_id,
        next_id=next_id,
    )
    return {"cardId": card_id, "status": status_value, "rank": rank}


@router.get("/api/cards/{card_id}/events")
def list_card_events(
    card_id: str,
    limit: str | None = None,
    since: str | None = None,
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    try:
        parsed = int(limit) if limit is not None else 500
    except ValueError:
        parsed = 500
    capped = max(1, min(parsed, 1000))

    query = select(CardEvent).where(CardEvent.card_id == card_id)
    if since:
        # `since` arrives as the ISO string of a previous event's `at` (legacy
        # SQLite compared strings; Postgres needs a real timestamp). An
        # unparseable value is ignored rather than 500ing the timeline.
        try:
            since_ts = datetime.fromisoformat(since)
        except ValueError:
            since_ts = None
        if since_ts is not None:
            query = query.where(CardEvent.at > since_ts)
    query = query.order_by(CardEvent.id.asc()).limit(capped)
    events = session.execute(query).scalars().all()
    return {"events": [e.public_dict() for e in events]}


# Re-exported so main.py mounts one router for the whole cards surface.
__all__ = ["router"]
