"""Persistence operations, one section per domain. Every function takes an
org-bound session (see ``db.org_session``) **and** an explicit ``org_id`` —
the queries filter by org in SQL (first line of defense) while the RLS
policies backstop them (see the ADR, D3: belt + suspenders).

Raises the domain errors below; routers translate them to HTTP. No HTTP or
wire-shape concerns in this module.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .derive import CardSnapshot, DerivedEvent, derive_events
from .models import (
    STATUS_IDS,
    CardEventRow,
    CardRankRow,
    CardRow,
    RetroRow,
    SavedViewRow,
    SprintCardRow,
    SprintRow,
    TriageBatchRow,
    TriageCardRow,
    utcnow,
)

RANK_BASE = 1024.0
RANK_STEP = 1024.0

_STATUS_ORDER = {status: index for index, status in enumerate(STATUS_IDS)}


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


class DomainValidationError(Exception):
    pass


# --------------------------------------------------------------------------
# Cards


def list_cards(session: Session, org_id: str) -> list[CardRow]:
    rows = session.scalars(select(CardRow).where(CardRow.org_id == org_id)).all()
    return sorted(rows, key=lambda c: (_STATUS_ORDER.get(c.status, 99), c.id))


def get_card(session: Session, org_id: str, card_id: str) -> CardRow | None:
    return session.get(CardRow, (org_id, card_id))


def create_card(
    session: Session,
    org_id: str,
    *,
    card_id: str,
    file: str,
    status: str = "backlog",
    frontmatter: dict[str, Any] | None = None,
    body: str = "",
) -> tuple[CardRow, list[CardEventRow]]:
    if get_card(session, org_id, card_id) is not None:
        raise ConflictError(f"card already exists: {card_id}")
    existing_file = session.scalar(
        select(CardRow).where(CardRow.org_id == org_id, CardRow.file == file)
    )
    if existing_file is not None:
        raise ConflictError(f"card file already exists: {file}")
    now = utcnow()
    card = CardRow(
        org_id=org_id,
        id=card_id,
        file=file,
        status=status,
        frontmatter=frontmatter or {},
        body=body,
        created_at=now,
        updated_at=now,
    )
    session.add(card)
    derived = derive_events(
        None, CardSnapshot(status=status, frontmatter=card.frontmatter), now
    )
    events = insert_card_events(session, org_id, card_id, derived)
    return card, events


def move_card(
    session: Session, org_id: str, card_id: str, new_status: str
) -> tuple[CardRow, float, list[CardEventRow]]:
    card = get_card(session, org_id, card_id)
    if card is None:
        raise NotFoundError("no such card")
    events: list[CardEventRow] = []
    if card.status != new_status:
        prev = CardSnapshot(status=card.status, frontmatter=dict(card.frontmatter))
        card.status = new_status
        card.updated_at = utcnow()
        derived = derive_events(
            prev,
            CardSnapshot(status=new_status, frontmatter=dict(card.frontmatter)),
            card.updated_at,
        )
        events = insert_card_events(session, org_id, card_id, derived)
    # Legacy parity: the move route always (re)appends the rank, including
    # same-status moves.
    rank = append_rank(session, org_id, card_id, new_status)
    return card, rank, events


def patch_frontmatter(
    session: Session, org_id: str, card_id: str, patch: dict[str, Any]
) -> tuple[CardRow, list[CardEventRow]]:
    """Apply an already-validated patch. ``None`` deletes the key."""
    card = get_card(session, org_id, card_id)
    if card is None:
        raise NotFoundError("no such card")
    prev = CardSnapshot(status=card.status, frontmatter=dict(card.frontmatter))
    fm = dict(card.frontmatter)
    for key, value in patch.items():
        if value is None:
            fm.pop(key, None)
        else:
            fm[key] = value
    if fm == prev.frontmatter:
        return card, []
    card.frontmatter = fm
    card.updated_at = utcnow()
    derived = derive_events(
        prev, CardSnapshot(status=card.status, frontmatter=fm), card.updated_at
    )
    events = insert_card_events(session, org_id, card_id, derived)
    return card, events


def append_card_body(
    session: Session, org_id: str, card_id: str, marker: str, text: str
) -> tuple[CardRow, bool]:
    """Append ``text`` under ``marker`` unless the marker is already present
    (idempotent under retry — legacy triage-merge semantics)."""
    card = get_card(session, org_id, card_id)
    if card is None:
        raise NotFoundError(f"no such card {card_id}")
    if marker in card.body:
        return card, False
    card.body = f"{card.body.rstrip()}\n\n{marker}\n\n{text.strip()}\n"
    card.updated_at = utcnow()
    return card, True


# --------------------------------------------------------------------------
# Ranks


def list_ranks(session: Session, org_id: str) -> list[CardRankRow]:
    return list(
        session.scalars(
            select(CardRankRow)
            .where(CardRankRow.org_id == org_id)
            .order_by(CardRankRow.card_id)
        )
    )


def _upsert_rank(
    session: Session, org_id: str, card_id: str, status: str, rank: float
) -> None:
    stmt = pg_insert(CardRankRow).values(
        org_id=org_id, card_id=card_id, status=status, rank=rank, updated_at=utcnow()
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["org_id", "card_id"],
            set_={"status": status, "rank": rank, "updated_at": utcnow()},
        )
    )


def _max_rank(session: Session, org_id: str, status: str) -> float | None:
    return session.scalar(
        select(func.max(CardRankRow.rank)).where(
            CardRankRow.org_id == org_id, CardRankRow.status == status
        )
    )


def append_rank(session: Session, org_id: str, card_id: str, status: str) -> float:
    current_max = _max_rank(session, org_id, status)
    rank = RANK_BASE if current_max is None else current_max + RANK_STEP
    _upsert_rank(session, org_id, card_id, status, rank)
    return rank


def set_rank_between(
    session: Session,
    org_id: str,
    card_id: str,
    status: str,
    prev_id: str | None,
    next_id: str | None,
) -> float:
    """Midpoint ranking; neighbor ranks are read server-side (client rank
    values are never trusted)."""

    def _rank_of(neighbor: str | None) -> float | None:
        if not neighbor:
            return None
        row = session.get(CardRankRow, (org_id, neighbor))
        return row.rank if row is not None else None

    prev_rank = _rank_of(prev_id)
    next_rank = _rank_of(next_id)
    if prev_rank is not None and next_rank is not None:
        rank = (prev_rank + next_rank) / 2
    elif prev_rank is not None:
        rank = prev_rank + RANK_STEP
    elif next_rank is not None:
        rank = next_rank - RANK_STEP
    else:
        current_max = _max_rank(session, org_id, status)
        rank = RANK_BASE if current_max is None else current_max + RANK_STEP
    _upsert_rank(session, org_id, card_id, status, rank)
    return rank


# --------------------------------------------------------------------------
# Card events


def insert_card_events(
    session: Session, org_id: str, card_id: str, derived: list[DerivedEvent]
) -> list[CardEventRow]:
    rows = [
        CardEventRow(
            org_id=org_id, card_id=card_id, type=d.type, at=d.at, details=d.details
        )
        for d in derived
    ]
    for row in rows:
        session.add(row)
    if rows:
        session.flush()  # populate identity ids for SSE payloads
    return rows


def list_card_events(
    session: Session,
    org_id: str,
    card_id: str,
    *,
    limit: int = 500,
    since: datetime | None = None,
) -> list[CardEventRow]:
    limit = max(1, min(limit or 500, 1000))
    stmt = (
        select(CardEventRow)
        .where(CardEventRow.org_id == org_id, CardEventRow.card_id == card_id)
        .order_by(CardEventRow.id.asc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(CardEventRow.at > since)
    return list(session.scalars(stmt))


# --------------------------------------------------------------------------
# Saved views

MAX_VIEW_NAME = 80
MAX_VIEW_PAYLOAD_BYTES = 16384


def _check_view_payload(payload: Any) -> None:
    try:
        encoded = json.dumps(payload).encode()
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("payload too large or not JSON-serializable") from exc
    if len(encoded) > MAX_VIEW_PAYLOAD_BYTES:
        raise DomainValidationError("payload too large or not JSON-serializable")


def list_views(session: Session, org_id: str, owner_sub: str) -> list[SavedViewRow]:
    return list(
        session.scalars(
            select(SavedViewRow)
            .where(SavedViewRow.org_id == org_id, SavedViewRow.owner_sub == owner_sub)
            .order_by(SavedViewRow.name.asc())
        )
    )


def get_view(
    session: Session, org_id: str, owner_sub: str, view_id: int
) -> SavedViewRow | None:
    view = session.get(SavedViewRow, view_id)
    if view is None or view.org_id != org_id or view.owner_sub != owner_sub:
        return None
    return view


def create_view(
    session: Session, org_id: str, owner_sub: str, name: str, payload: Any
) -> SavedViewRow:
    _check_view_payload(payload)
    duplicate = session.scalar(
        select(SavedViewRow).where(
            SavedViewRow.org_id == org_id,
            SavedViewRow.owner_sub == owner_sub,
            SavedViewRow.name == name,
        )
    )
    if duplicate is not None:
        raise ConflictError(f"view name already exists: {name}")
    now = utcnow()
    view = SavedViewRow(
        org_id=org_id,
        owner_sub=owner_sub,
        name=name,
        payload=payload,
        created_at=now,
        updated_at=now,
    )
    session.add(view)
    session.flush()
    return view


def update_view(
    session: Session,
    org_id: str,
    owner_sub: str,
    view_id: int,
    *,
    name: str | None = None,
    payload: Any = ...,
) -> SavedViewRow:
    view = get_view(session, org_id, owner_sub, view_id)
    if view is None:
        raise NotFoundError("no such view")
    if name is not None:
        view.name = name
    if payload is not ...:
        _check_view_payload(payload)
        view.payload = payload
    view.updated_at = utcnow()
    return view


def delete_view(session: Session, org_id: str, owner_sub: str, view_id: int) -> None:
    view = get_view(session, org_id, owner_sub, view_id)
    if view is None:
        raise NotFoundError("no such view")
    session.delete(view)


# --------------------------------------------------------------------------
# Sprints


def list_sprints(
    session: Session, org_id: str, *, include_archived: bool = False
) -> list[tuple[SprintRow, int, int]]:
    stmt = (
        select(
            SprintRow,
            func.count(SprintCardRow.card_id),
            func.coalesce(func.sum(SprintCardRow.planned_points), 0),
        )
        .outerjoin(
            SprintCardRow,
            (SprintCardRow.sprint_id == SprintRow.id)
            & (SprintCardRow.org_id == SprintRow.org_id),
        )
        .where(SprintRow.org_id == org_id)
        .group_by(SprintRow.id)
        .order_by(SprintRow.starts_at.desc())
    )
    if not include_archived:
        stmt = stmt.where(SprintRow.archived_at.is_(None))
    return [(row[0], int(row[1]), int(row[2])) for row in session.execute(stmt)]


def get_sprint(session: Session, org_id: str, sprint_id: int) -> SprintRow | None:
    sprint = session.get(SprintRow, sprint_id)
    if sprint is None or sprint.org_id != org_id:
        return None
    return sprint


def create_sprint(session: Session, org_id: str, **fields: Any) -> SprintRow:
    sprint = SprintRow(org_id=org_id, **fields)
    session.add(sprint)
    session.flush()
    return sprint


def sprint_cards(session: Session, org_id: str, sprint_id: int) -> list[SprintCardRow]:
    return list(
        session.scalars(
            select(SprintCardRow)
            .where(SprintCardRow.org_id == org_id, SprintCardRow.sprint_id == sprint_id)
            .order_by(SprintCardRow.card_id)
        )
    )


def upsert_sprint_card(
    session: Session,
    org_id: str,
    sprint_id: int,
    card_id: str,
    planned_points: int | None,
) -> None:
    stmt = pg_insert(SprintCardRow).values(
        org_id=org_id, sprint_id=sprint_id, card_id=card_id, planned_points=planned_points
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["org_id", "sprint_id", "card_id"],
            set_={"planned_points": planned_points},
        )
    )


def remove_sprint_card(
    session: Session, org_id: str, sprint_id: int, card_id: str
) -> None:
    session.execute(
        delete(SprintCardRow).where(
            SprintCardRow.org_id == org_id,
            SprintCardRow.sprint_id == sprint_id,
            SprintCardRow.card_id == card_id,
        )
    )


# --------------------------------------------------------------------------
# Retros


def list_retros(session: Session, org_id: str) -> list[RetroRow]:
    return list(
        session.scalars(
            select(RetroRow)
            .where(RetroRow.org_id == org_id)
            .order_by(RetroRow.held_on.desc())
        )
    )


def get_retro(session: Session, org_id: str, retro_id: int) -> RetroRow | None:
    retro = session.get(RetroRow, retro_id)
    if retro is None or retro.org_id != org_id:
        return None
    return retro


def create_retro(
    session: Session,
    org_id: str,
    *,
    sprint_id: int | None,
    held_on: str,
    summary: str | None,
) -> RetroRow:
    retro = RetroRow(org_id=org_id, sprint_id=sprint_id, held_on=held_on, summary=summary)
    session.add(retro)
    session.flush()
    return retro


# --------------------------------------------------------------------------
# Triage


def _staged_cards(session: Session, org_id: str, batch_id: str) -> list[TriageCardRow]:
    return list(
        session.scalars(
            select(TriageCardRow)
            .where(
                TriageCardRow.org_id == org_id,
                TriageCardRow.batch_id == batch_id,
                TriageCardRow.state == "staged",
            )
            .order_by(TriageCardRow.card_id)
        )
    )


def list_triage_batches(
    session: Session, org_id: str
) -> list[tuple[TriageBatchRow, list[TriageCardRow]]]:
    batches = session.scalars(
        select(TriageBatchRow)
        .where(TriageBatchRow.org_id == org_id, TriageBatchRow.state == "ready")
        .order_by(TriageBatchRow.batch_id)
    ).all()
    result = []
    for batch in batches:
        staged = _staged_cards(session, org_id, batch.batch_id)
        if staged:
            result.append((batch, staged))
    return result


def create_triage_batch(
    session: Session,
    org_id: str,
    *,
    batch_id: str,
    story: str | None,
    cards: list[dict[str, Any]],
) -> int:
    if session.get(TriageBatchRow, (org_id, batch_id)) is not None:
        raise ConflictError(f"batch already exists: {batch_id}")
    session.add(TriageBatchRow(org_id=org_id, batch_id=batch_id, story=story))
    for spec in cards:
        file = spec["file"]
        frontmatter = spec.get("frontmatter") or {}
        raw_id = frontmatter.get("id")
        card_id = (
            raw_id
            if isinstance(raw_id, str) and raw_id.strip()
            else file.removesuffix(".md")
        )
        raw_title = spec.get("title") or frontmatter.get("title")
        title = raw_title if isinstance(raw_title, str) and raw_title.strip() else card_id
        session.add(
            TriageCardRow(
                org_id=org_id,
                batch_id=batch_id,
                file=file,
                card_id=card_id,
                title=title,
                frontmatter=frontmatter,
                body=spec.get("body") or "",
            )
        )
    return len(cards)


def get_staged_card(
    session: Session, org_id: str, batch_id: str, file: str
) -> TriageCardRow | None:
    row = session.get(TriageCardRow, (org_id, batch_id, file))
    if row is None or row.state != "staged":
        return None
    return row


def finalize_batch_if_drained(session: Session, org_id: str, batch_id: str) -> None:
    if not _staged_cards(session, org_id, batch_id):
        session.execute(
            update(TriageBatchRow)
            .where(TriageBatchRow.org_id == org_id, TriageBatchRow.batch_id == batch_id)
            .values(state="finalized")
        )


def promote_triage_card(
    session: Session, org_id: str, batch_id: str, file: str
) -> tuple[CardRow, float, list[CardEventRow]]:
    staged = get_staged_card(session, org_id, batch_id, file)
    if staged is None:
        raise NotFoundError("no staged card")
    try:
        card, events = create_card(
            session,
            org_id,
            card_id=staged.card_id,
            file=staged.file,
            status="backlog",
            frontmatter=dict(staged.frontmatter),
            body=staged.body,
        )
    except ConflictError as exc:
        raise ConflictError(f"Refusing to overwrite: {exc}") from exc
    rank = append_rank(session, org_id, staged.card_id, "backlog")
    staged.state = "promoted"
    staged.updated_at = utcnow()
    finalize_batch_if_drained(session, org_id, batch_id)
    return card, rank, events


def decline_triage_card(
    session: Session, org_id: str, batch_id: str, file: str, *, state: str = "declined"
) -> None:
    staged = get_staged_card(session, org_id, batch_id, file)
    if staged is None:
        raise NotFoundError("no staged card")
    staged.state = state
    staged.updated_at = utcnow()
    finalize_batch_if_drained(session, org_id, batch_id)
