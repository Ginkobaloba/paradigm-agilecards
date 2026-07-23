"""Triage: review planned cards batch-by-batch (legacy ``routes/triage.ts``).

Wire shapes and error strings are contract: ``TriageCard`` comes from
``StagedCard.triage_dict()``, batches list only when "ready" with at least one
still-staged card. Legacy staged cards as markdown under ``_staging/<batch>/``
and enforced safe-basename regexes against path traversal; with DB rows the
path params are opaque lookup keys and traversal is moot.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import AuditLog
from ..auth import ParadigmClaims
from ..deps import get_audit, get_session, require_claims
from ..models import Card, StagedCard, StoryBatch
from ..repo import add_card_event, append_rank, get_card, queue_sse

router = APIRouter()

# Annotated dependency aliases (the ruff pinned here flags `= Depends(...)`
# defaults as B008; the Annotated form is equivalent and lint-clean).
Claims = Annotated[ParadigmClaims, Depends(require_claims)]
OrgSession = Annotated[Session, Depends(get_session)]
Audit = Annotated[AuditLog, Depends(get_audit)]


def _no_such_staged_card() -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "no such staged card"})


def _resolve_staged(session: Session, batch_id: str, file: str) -> StagedCard:
    """Look up one still-staged card, enforcing the legacy error contract:
    unknown batch/file (or already promoted/declined) -> 404, a batch whose
    planner has not finished -> 409."""
    batch = session.execute(
        select(StoryBatch).where(StoryBatch.batch_id == batch_id)
    ).scalar_one_or_none()
    if batch is None:
        raise _no_such_staged_card()
    if batch.state == "planning":
        raise HTTPException(status_code=409, detail={"error": "batch is still planning"})
    staged = session.execute(
        select(StagedCard).where(StagedCard.batch_id == batch_id, StagedCard.file == file)
    ).scalar_one_or_none()
    if staged is None or staged.state != "staged":
        raise _no_such_staged_card()
    return staged


@router.get("/api/triage")
def list_triage(claims: Claims, session: OrgSession) -> dict:
    batches = (
        session.execute(
            select(StoryBatch)
            .where(StoryBatch.state == "ready")
            .order_by(StoryBatch.batch_id.asc())
        )
        .scalars()
        .all()
    )
    out = []
    for batch in batches:
        staged = (
            session.execute(
                select(StagedCard)
                .where(StagedCard.batch_id == batch.batch_id, StagedCard.state == "staged")
                .order_by(StagedCard.card_id.asc())
            )
            .scalars()
            .all()
        )
        if not staged:  # fully triaged batches drop off the list (legacy)
            continue
        out.append(
            {
                "batchId": batch.batch_id,
                "story": batch.story[:200] if batch.story is not None else None,
                "cards": [s.triage_dict() for s in staged],
            }
        )
    return {"batches": out}


@router.post("/api/triage/{batch_id}/cards/{file}/promote")
def promote_staged_card(
    batch_id: str,
    file: str,
    claims: Claims,
    session: OrgSession,
    audit: Audit,
) -> dict:
    staged = _resolve_staged(session, batch_id, file)
    if get_card(session, staged.card_id) is not None:
        raise HTTPException(
            status_code=409, detail={"error": "card already exists in backlog"}
        )
    # Same insert semantics as stories approve: real card, bottom-of-backlog
    # rank, a `discovered` timeline seed, and the card-added live event.
    card = Card(
        org_id=claims.org_id,
        id=staged.card_id,
        status="backlog",
        frontmatter=staged.frontmatter,
        body=staged.body,
    )
    session.add(card)
    session.flush()
    rank = append_rank(session, org_id=claims.org_id, card_id=card.id, status="backlog")
    add_card_event(session, org_id=claims.org_id, card_id=card.id, type_="discovered", details={})
    queue_sse(
        session,
        claims.org_id,
        {"type": "card-added", "cardId": card.id, "status": "backlog"},
    )
    staged.state = "promoted"
    audit.emit(
        action="triage.promoted",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="staged_card",
        resource_id=staged.card_id,
        detail={"batchId": batch_id, "file": file},
        session=session,
    )
    return {"id": card.id, "status": "backlog", "rank": rank}


@router.post("/api/triage/{batch_id}/cards/{file}/decline")
def decline_staged_card(
    batch_id: str,
    file: str,
    claims: Claims,
    session: OrgSession,
    audit: Audit,
) -> dict:
    staged = _resolve_staged(session, batch_id, file)
    staged.state = "declined"
    audit.emit(
        action="triage.declined",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="staged_card",
        resource_id=staged.card_id,
        detail={"batchId": batch_id, "file": file},
        session=session,
    )
    return {"ok": True}


@router.post("/api/triage/{batch_id}/cards/{file}/merge")
def merge_staged_card(
    batch_id: str,
    file: str,
    claims: Claims,
    session: OrgSession,
    audit: Audit,
    body: Annotated[Any, Body()] = None,
) -> dict:
    target_id = body.get("targetId") if isinstance(body, dict) else None
    if not isinstance(target_id, str) or not target_id:
        raise HTTPException(status_code=400, detail={"error": "targetId is required"})
    staged = _resolve_staged(session, batch_id, file)
    target = get_card(session, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"error": "no such card"})

    # Retry-safe (legacy contract): if the absorbed-from marker is already in
    # the target body, skip the append but still decline and answer ok.
    marker = f"## Absorbed from triage ({staged.card_id})"
    if marker not in (target.body or ""):
        target.body = (target.body or "") + f"\n\n{marker}\n\n{staged.body}\n"
        session.flush()
    queue_sse(
        session,
        claims.org_id,
        {"type": "card-updated", "cardId": target.id, "status": target.status},
    )
    staged.state = "declined"
    audit.emit(
        action="triage.merged",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="staged_card",
        resource_id=staged.card_id,
        detail={"batchId": batch_id, "file": file, "targetId": target.id},
        session=session,
    )
    return {"ok": True, "targetId": target.id}
