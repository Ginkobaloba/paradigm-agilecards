"""Wire-shape serializers: ORM rows -> the exact JSON the frontend expects
(camelCase, ``mtimeMs`` float epoch-ms, ISO strings; retros stay snake_case —
a preserved legacy quirk). Contract: docs/board/CARDS_API_CONTRACT.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import (
    CardEventRow,
    CardRankRow,
    CardRow,
    RetroRow,
    SavedViewRow,
    SprintCardRow,
    SprintRow,
)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch_ms(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0


def card_summary(card: CardRow) -> dict[str, Any]:
    return {
        "id": card.id,
        "file": card.file,
        "status": card.status,
        "frontmatter": card.frontmatter,
        "mtimeMs": _epoch_ms(card.updated_at),
    }


def card_detail(card: CardRow) -> dict[str, Any]:
    return {**card_summary(card), "body": card.body}


def rank_row(rank: CardRankRow) -> dict[str, Any]:
    return {"cardId": rank.card_id, "status": rank.status, "rank": rank.rank}


def card_event(event: CardEventRow) -> dict[str, Any]:
    return {
        "id": event.id,
        "cardId": event.card_id,
        "type": event.type,
        "at": _iso(event.at),
        "details": event.details,
    }


def saved_view(view: SavedViewRow) -> dict[str, Any]:
    return {
        "id": view.id,
        # Legacy scoped views by opaque-token id; the field is kept so the
        # frontend type still parses, pinned to 0 (contract deviation #3).
        "tokenId": 0,
        "name": view.name,
        "payload": view.payload,
        "createdAt": _iso(view.created_at),
        "updatedAt": _iso(view.updated_at),
    }


def sprint_shape(sprint: SprintRow) -> dict[str, Any]:
    return {
        "id": sprint.id,
        "name": sprint.name,
        "startsAt": sprint.starts_at,
        "endsAt": sprint.ends_at,
        "goal": sprint.goal,
        "status": sprint.status,
        "pointsTarget": sprint.points_target,
        "dollarTarget": sprint.dollar_target,
        "reviewHoursTarget": sprint.review_hours_target,
        "archivedAt": sprint.archived_at,
        "createdAt": _iso(sprint.created_at),
    }


def sprint_card_link(link: SprintCardRow) -> dict[str, Any]:
    return {
        "sprintId": link.sprint_id,
        "cardId": link.card_id,
        "plannedPoints": link.planned_points,
    }


def retro_row(retro: RetroRow) -> dict[str, Any]:
    return {
        "id": retro.id,
        "sprint_id": retro.sprint_id,
        "held_on": retro.held_on,
        "summary": retro.summary,
        "created_at": _iso(retro.created_at),
    }
