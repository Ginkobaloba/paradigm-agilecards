"""Shared persistence helpers: rank math, card events, post-commit SSE.

Rank algorithm is a faithful port of legacy ``db/ranks.ts`` -- concurrent
clients must compute identical midpoints, so the constants and branch order
are contract, not style:

- both neighbors present -> (prev + next) / 2
- prev only             -> prev + RANK_STEP
- next only             -> next - RANK_STEP
- neither               -> max(rank in status) + RANK_STEP, or RANK_BASE

SSE publishing is deliberately post-commit: routes queue payloads on the
session (``queue_sse``) and ``deps.get_session`` publishes them only after the
transaction commits, so subscribers never see events for rolled-back writes.
(Legacy published from a filesystem watcher, which had the same
only-after-the-write-landed property.)
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Card, CardEvent, CardRank

RANK_BASE = 1024.0
RANK_STEP = 1024.0

_SSE_KEY = "sse_pending"


def queue_sse(session: Session, org_id: str, payload: dict) -> None:
    """Queue an SSE payload for delivery after this transaction commits."""
    session.info.setdefault(_SSE_KEY, []).append((org_id, payload))


def drain_sse(session: Session) -> list[tuple[str, dict]]:
    return session.info.pop(_SSE_KEY, [])


def get_card(session: Session, card_id: str) -> Card | None:
    """Fetch one card. RLS already scopes to the bound org; the explicit
    org filter would be redundant here and its absence is exactly what the
    RLS suite proves safe."""
    return session.get(Card, {"org_id": _bound_org(session), "id": card_id})


def _bound_org(session: Session) -> str:
    # The composite PK needs the org half; read it back from the GUC that
    # deps.get_session bound. Single source of truth: the verified token.
    row = session.execute(
        select(func.nullif(func.current_setting("app.current_org", True), ""))
    ).scalar_one()
    if row is None:
        raise RuntimeError("no org context bound; get_session must wrap every tenant request")
    return row


def add_card_event(
    session: Session,
    *,
    org_id: str,
    card_id: str,
    type_: str,
    details: dict | None = None,
) -> CardEvent:
    """Insert a timeline event and queue its ``card-event-added`` SSE."""
    event = CardEvent(org_id=org_id, card_id=card_id, type=type_, details=details)
    session.add(event)
    session.flush()
    session.refresh(event)  # populate server-side id/at for the wire payload
    queue_sse(
        session,
        org_id,
        {"type": "card-event-added", "cardId": card_id, "event": event.public_dict()},
    )
    return event


def append_rank(session: Session, *, org_id: str, card_id: str, status: str) -> float:
    """Assign a fresh rank at the bottom of ``status`` (legacy appendRank)."""
    max_rank = session.execute(
        select(func.max(CardRank.rank)).where(CardRank.status == status)
    ).scalar_one()
    rank = (max_rank + RANK_STEP) if max_rank is not None else RANK_BASE
    _upsert_rank(session, org_id=org_id, card_id=card_id, status=status, rank=rank)
    return rank


def set_rank_between(
    session: Session,
    *,
    org_id: str,
    card_id: str,
    status: str,
    prev_id: str | None,
    next_id: str | None,
) -> float:
    """Midpoint rank placement (legacy setRankBetween). Neighbor ranks are
    looked up server-side by id -- never trusted from the client."""
    prev_rank = _rank_of(session, prev_id) if prev_id else None
    next_rank = _rank_of(session, next_id) if next_id else None

    if prev_rank is not None and next_rank is not None:
        rank = (prev_rank + next_rank) / 2
    elif prev_rank is not None:
        rank = prev_rank + RANK_STEP
    elif next_rank is not None:
        rank = next_rank - RANK_STEP
    else:
        max_rank = session.execute(
            select(func.max(CardRank.rank)).where(CardRank.status == status)
        ).scalar_one()
        rank = (max_rank + RANK_STEP) if max_rank is not None else RANK_BASE

    _upsert_rank(session, org_id=org_id, card_id=card_id, status=status, rank=rank)
    return rank


def _rank_of(session: Session, card_id: str) -> float | None:
    return session.execute(
        select(CardRank.rank).where(CardRank.card_id == card_id)
    ).scalar_one_or_none()


def _upsert_rank(
    session: Session, *, org_id: str, card_id: str, status: str, rank: float
) -> None:
    row = session.get(CardRank, {"org_id": org_id, "card_id": card_id})
    if row is None:
        session.add(CardRank(org_id=org_id, card_id=card_id, status=status, rank=rank))
    else:
        row.status = status
        row.rank = rank
    session.flush()
