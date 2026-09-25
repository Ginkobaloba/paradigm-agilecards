"""Triage: staged batches awaiting promote/decline/merge, plus the service
ingest endpoint that replaces "the engine writes files into _staging/"."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session, sessionmaker

from .. import repository as repo
from .. import wire
from ..auth import ParadigmClaims
from ..bus import OrgEventBus
from ..deps import require_any_role, require_claims
from .common import err, get_bus, get_db, mutation, reading, require_json_object

router = APIRouter()

# Legacy path-safety patterns, kept as input hygiene even though nothing
# touches a filesystem anymore.
_SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
_SAFE_BATCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _check_batch_id(batch_id: str) -> str:
    if not _SAFE_BATCH.fullmatch(batch_id) or ".." in batch_id:
        raise err(400, "invalid batch id")
    return batch_id


def _check_file(file: str) -> str:
    if not _SAFE_FILE.fullmatch(file) or ".." in file:
        raise err(400, "invalid file name")
    return file


def _triage_card(card: Any) -> dict[str, Any]:
    fm = card.frontmatter or {}
    tier = fm.get("points")
    est = fm.get("estimated_tokens")
    depends = fm.get("depends_on")
    body = (card.body or "").strip()
    return {
        "id": card.card_id,
        "title": card.title,
        "file": card.file,
        "bodyExcerpt": body[:280],
        "tier": tier if isinstance(tier, (int, float)) and not isinstance(tier, bool) else None,
        "model": fm.get("model") if isinstance(fm.get("model"), str) else None,
        "estimatedTokens": est if isinstance(est, (int, float)) and not isinstance(est, bool) else None,
        "dependsOn": [d for d in depends if isinstance(d, str)] if isinstance(depends, list) else [],
    }


@router.get("/api/triage")
def list_triage(
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
) -> dict:
    with reading(factory, claims) as session:
        batches = repo.list_triage_batches(session, claims.org_id)
        return {
            "batches": [
                {
                    "batchId": batch.batch_id,
                    "story": (batch.story or None) and batch.story[:200],
                    "cards": [_triage_card(c) for c in cards],
                }
                for batch, cards in batches
            ]
        }


@router.post("/api/triage/batches", status_code=201)
def ingest_batch(
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_any_role("service", "admin")),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    body = require_json_object(body)
    batch_id = body.get("batchId")
    if not isinstance(batch_id, str):
        raise err(400, "batchId is required")
    _check_batch_id(batch_id)
    cards = body.get("cards")
    if not isinstance(cards, list) or not cards:
        raise err(400, "cards must be a non-empty array")
    for spec in cards:
        if not isinstance(spec, dict) or not isinstance(spec.get("file"), str):
            raise err(400, "every card needs a file name")
        _check_file(spec["file"])
        fm = spec.get("frontmatter")
        if fm is not None and not isinstance(fm, dict):
            raise err(400, "frontmatter must be an object")
    story = body.get("story")
    story = story if isinstance(story, str) and story.strip() else None

    with mutation(factory, claims, bus) as ctx:
        count = repo.create_triage_batch(
            ctx.session, claims.org_id, batch_id=batch_id, story=story, cards=cards
        )
        ctx.audit(
            "triage.ingest",
            resource_type="triage_batch",
            resource_id=batch_id,
            details={"cardCount": count},
        )
        response = {"batchId": batch_id, "cardCount": count}
    return response


@router.post("/api/triage/{batch_id}/cards/{file}/promote")
def promote(
    batch_id: str,
    file: str,
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    _check_batch_id(batch_id)
    _check_file(file)
    with mutation(factory, claims, bus) as ctx:
        card, rank, events = repo.promote_triage_card(
            ctx.session, claims.org_id, batch_id, file
        )
        ctx.audit(
            "triage.promote",
            resource_type="triage_batch",
            resource_id=batch_id,
            details={"file": file, "cardId": card.id},
        )
        ctx.emit({"type": "card-added", "cardId": card.id, "status": card.status})
        for event in events:
            ctx.emit(
                {"type": "card-event-added", "cardId": card.id, "event": wire.card_event(event)}
            )
        response = {"id": card.id, "status": card.status, "rank": rank}
    return response


@router.post("/api/triage/{batch_id}/cards/{file}/decline")
def decline(
    batch_id: str,
    file: str,
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    _check_batch_id(batch_id)
    _check_file(file)
    with mutation(factory, claims, bus) as ctx:
        repo.decline_triage_card(ctx.session, claims.org_id, batch_id, file)
        ctx.audit(
            "triage.decline",
            resource_type="triage_batch",
            resource_id=batch_id,
            details={"file": file},
        )
    return {"ok": True}


@router.post("/api/triage/{batch_id}/cards/{file}/merge")
def merge(
    batch_id: str,
    file: str,
    body: dict[str, Any] = Body(...),
    claims: ParadigmClaims = Depends(require_claims),
    factory: sessionmaker[Session] = Depends(get_db),
    bus: OrgEventBus = Depends(get_bus),
) -> dict:
    _check_batch_id(batch_id)
    _check_file(file)
    target_id = require_json_object(body).get("targetId")
    if not isinstance(target_id, str) or not target_id.strip():
        raise err(400, "targetId is required")
    with mutation(factory, claims, bus) as ctx:
        staged = repo.get_staged_card(ctx.session, claims.org_id, batch_id, file)
        if staged is None:
            raise err(404, "no staged card")
        marker = f"## Absorbed from triage ({staged.card_id})"
        provenance = f"_from batch {batch_id} / {file}_\n\n{staged.body}"
        target, changed = repo.append_card_body(
            ctx.session, claims.org_id, target_id, marker, provenance
        )
        repo.decline_triage_card(ctx.session, claims.org_id, batch_id, file, state="merged")
        ctx.audit(
            "triage.merge",
            resource_type="triage_batch",
            resource_id=batch_id,
            details={"file": file, "targetId": target_id, "appended": changed},
        )
        if changed:
            ctx.emit(
                {"type": "card-updated", "cardId": target.id, "status": target.status}
            )
    return {"ok": True, "targetId": target_id}
