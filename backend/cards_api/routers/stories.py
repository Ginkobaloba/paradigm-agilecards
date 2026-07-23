"""Stories: submit-for-planning + approve/cancel/pending (legacy
``routes/stories.ts``, SSE-over-POST).

Submit validates as plain JSON *before* any streaming begins (legacy behavior:
a 400/501 is a normal error response, never a stream). Once valid, the
response is an SSE stream of ``progress`` frames followed by one ``dry_run``
frame -- and nothing after it (no ``complete`` event; legacy contract). The
planner behind the stream is a seam (``cards_api.planner``): the legacy CLI
invoker shells out to ``claude`` on the host, which the containerized
deployment does not have, so an unconfigured planner answers 501 and the real
CLI port belongs to the runner-unification chunk (P2).

The stream outlives the request transaction, so this route does NOT use
``get_session``; it opens short org-bound transactions per step instead.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import AuditLog
from ..auth import ParadigmClaims
from ..db import Database
from ..deps import get_audit, get_bus, get_database, get_session, require_claims
from ..events import EventBus
from ..models import Card, StagedCard, StoryBatch
from ..planner import PlannerError, load_planner
from ..repo import add_card_event, append_rank, get_card, queue_sse
from .sse import _SSE_HEADERS

router = APIRouter()

MAX_STORY_BYTES = 65536
BATCH_TTL = timedelta(hours=1)

# Annotated dependency aliases (the ruff pinned here flags `= Depends(...)`
# defaults as B008; the Annotated form is equivalent and lint-clean).
Claims = Annotated[ParadigmClaims, Depends(require_claims)]
OrgSession = Annotated[Session, Depends(get_session)]
Audit = Annotated[AuditLog, Depends(get_audit)]


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": message})


def _frame(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.post("/api/stories/submit")
async def submit_story(
    claims: Claims,
    database: Annotated[Database | None, Depends(get_database)],
    bus: Annotated[EventBus, Depends(get_bus)],
    audit: Audit,
    body: Annotated[Any, Body()] = None,
) -> StreamingResponse:
    story = body.get("story") if isinstance(body, dict) else None
    if not isinstance(story, str) or not story:
        raise _bad_request("story is required")
    if len(story.encode("utf-8")) > MAX_STORY_BYTES:
        raise _bad_request(f"story too large (max {MAX_STORY_BYTES} bytes)")
    mode = body.get("mode")
    if mode is None:
        mode = "full"
    if mode not in ("full", "lean"):
        raise _bad_request("mode must be full or lean")
    # JSON true only; any other value (including truthy strings) means false.
    deep_planning = body.get("deep_planning") is True
    project_path = body.get("project_path")
    project_path = project_path if isinstance(project_path, str) else None

    # Read at request time so tests (and operators) can flip the env var
    # without restarting the process.
    planner = load_planner(os.environ.get("PARADIGM_STORIES_PLANNER"))
    if planner is None:
        raise HTTPException(
            status_code=501,
            detail={"error": "story planning is not available on this deployment"},
        )
    if database is None:
        raise HTTPException(status_code=503, detail={"error": "database_unavailable"})

    org_id = claims.org_id
    actor_sub = claims.sub

    async def stream() -> AsyncIterator[str]:
        batch_id = uuid.uuid4().hex[:12]
        with database.org_session(org_id) as session:
            session.add(
                StoryBatch(org_id=org_id, batch_id=batch_id, story=story, state="planning")
            )
            audit.emit(
                action="story.submitted",
                org_id=org_id,
                actor_sub=actor_sub,
                resource_type="story_batch",
                resource_id=batch_id,
                session=session,
            )

        # DemoPlanner is synchronous; calling it inline in the async generator
        # buffers its progress callbacks until it returns. Acceptable for the
        # demo path -- the real CLI planner (runner-unification chunk) will
        # stream incrementally.
        progress_frames: list[dict] = []

        def on_progress(update: dict) -> None:
            progress_frames.append({**update, "batch_id": batch_id})

        try:
            result = planner.plan(
                story=story,
                project_path=project_path,
                mode=mode,
                deep_planning=deep_planning,
                progress=on_progress,
            )
        except PlannerError as exc:
            for frame in progress_frames:
                yield _frame("progress", frame)
            with database.org_session(org_id) as session:
                batch = session.get(
                    StoryBatch, {"org_id": org_id, "batch_id": batch_id}
                )
                if batch is not None:
                    batch.state = "cancelled"
            yield _frame("error", {"message": str(exc), "stage": exc.stage})
            return

        for frame in progress_frames:
            yield _frame("progress", frame)

        with database.org_session(org_id) as session:
            staged_rows: list[StagedCard] = []
            for planned in result.cards:
                row = StagedCard(
                    org_id=org_id,
                    batch_id=batch_id,
                    file=planned.file,
                    card_id=planned.card_id,
                    title=planned.title,
                    frontmatter=planned.frontmatter,
                    body=planned.body,
                    state="staged",
                )
                session.add(row)
                staged_rows.append(row)
            batch = session.get(StoryBatch, {"org_id": org_id, "batch_id": batch_id})
            assert batch is not None, f"story batch {batch_id} vanished mid-stream"
            batch.state = "ready"
            batch.manifest = result.manifest
            batch.expires_at = datetime.now(UTC) + BATCH_TTL
            cards_payload = [row.triage_dict() for row in staged_rows]

        histogram: dict[str, int] = {}
        edges: list[list[str]] = []
        claimable = 0
        for card in cards_payload:
            if card["tier"] is not None:
                key = str(card["tier"])
                histogram[key] = histogram.get(key, 0) + 1
            if not card["dependsOn"]:
                claimable += 1
            for dep in card["dependsOn"]:
                edges.append([card["id"], dep])  # [from, to] = card -> its dependency

        yield _frame(
            "dry_run",
            {
                "batch_id": batch_id,
                "cards": cards_payload,
                "histogram": histogram,
                "depends_on_edges": edges,
                "claimable_count": claimable,
                "mode": mode,
                "deep_planning": deep_planning,
            },
        )
        # Legacy contract: the stream ends after dry_run; no "complete" event.

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _pending_batch(session: Session, batch_id: str) -> StoryBatch | None:
    batch = session.execute(
        select(StoryBatch).where(StoryBatch.batch_id == batch_id)
    ).scalar_one_or_none()
    if (
        batch is None
        or batch.state != "ready"
        or batch.expires_at is None
        or batch.expires_at <= datetime.now(UTC)
    ):
        return None
    return batch


@router.post("/api/stories/{batch_id}/approve")
def approve_batch(
    batch_id: str,
    claims: Claims,
    session: OrgSession,
    audit: Audit,
) -> dict:
    batch = _pending_batch(session, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=404, detail={"error": f"no pending batch {batch_id}"}
        )
    staged_rows = (
        session.execute(
            select(StagedCard)
            .where(StagedCard.batch_id == batch_id, StagedCard.state == "staged")
            .order_by(StagedCard.card_id.asc())
        )
        .scalars()
        .all()
    )
    written = 0
    for staged in staged_rows:
        if get_card(session, staged.card_id) is not None:
            # Raising rolls back the whole approval (get_session): all cards
            # land or none do.
            raise HTTPException(
                status_code=409, detail={"error": "card already exists in backlog"}
            )
        # Same insert semantics as triage promote.
        card = Card(
            org_id=claims.org_id,
            id=staged.card_id,
            status="backlog",
            frontmatter=staged.frontmatter,
            body=staged.body,
        )
        session.add(card)
        session.flush()
        append_rank(session, org_id=claims.org_id, card_id=card.id, status="backlog")
        add_card_event(
            session, org_id=claims.org_id, card_id=card.id, type_="discovered", details={}
        )
        queue_sse(
            session,
            claims.org_id,
            {"type": "card-added", "cardId": card.id, "status": "backlog"},
        )
        staged.state = "promoted"
        written += 1
    batch.state = "promoted"
    audit.emit(
        action="story.approved",
        org_id=claims.org_id,
        actor_sub=claims.sub,
        resource_type="story_batch",
        resource_id=batch_id,
        detail={"cardsWritten": written},
        session=session,
    )
    return {"batchId": batch_id, "cardsWritten": written}


@router.post("/api/stories/{batch_id}/cancel")
def cancel_batch(
    batch_id: str,
    claims: Claims,
    session: OrgSession,
    audit: Audit,
) -> dict:
    # Best-effort (legacy): the frontend ignores errors, so unknown or
    # already-finished batches still answer ok.
    batch = session.execute(
        select(StoryBatch).where(StoryBatch.batch_id == batch_id)
    ).scalar_one_or_none()
    if batch is not None and batch.state != "promoted":
        batch.state = "cancelled"
        staged_rows = (
            session.execute(
                select(StagedCard).where(
                    StagedCard.batch_id == batch_id, StagedCard.state == "staged"
                )
            )
            .scalars()
            .all()
        )
        for staged in staged_rows:
            staged.state = "declined"
        audit.emit(
            action="story.cancelled",
            org_id=claims.org_id,
            actor_sub=claims.sub,
            resource_type="story_batch",
            resource_id=batch_id,
            session=session,
        )
    return {"ok": True}


@router.get("/api/stories/pending")
def list_pending(claims: Claims, session: OrgSession) -> dict:
    now = datetime.now(UTC)
    batches = (
        session.execute(
            select(StoryBatch)
            .where(StoryBatch.state == "ready", StoryBatch.expires_at > now)
            .order_by(StoryBatch.batch_id.asc())
        )
        .scalars()
        .all()
    )
    pending = []
    for batch in batches:
        staged_count = len(
            session.execute(
                select(StagedCard.card_id).where(
                    StagedCard.batch_id == batch.batch_id, StagedCard.state == "staged"
                )
            ).all()
        )
        pending.append(
            {
                "batchId": batch.batch_id,
                "story": batch.story[:200] if batch.story is not None else None,
                "cardCount": staged_count,
                "expiresAt": batch.expires_at.isoformat() if batch.expires_at else None,
            }
        )
    return {"pending": pending}
