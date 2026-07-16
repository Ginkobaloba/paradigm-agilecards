"""Rank listing (legacy ``GET /api/ranks``). The frontend treats failures as
an empty list, but the success shape must match exactly."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import ParadigmClaims
from ..deps import get_session, require_claims
from ..models import CardRank

router = APIRouter()


@router.get("/api/ranks")
def list_ranks(
    claims: ParadigmClaims = Depends(require_claims),
    session: Session = Depends(get_session),
) -> dict:
    ranks = session.execute(select(CardRank)).scalars().all()
    return {"ranks": [r.public_dict() for r in ranks]}
