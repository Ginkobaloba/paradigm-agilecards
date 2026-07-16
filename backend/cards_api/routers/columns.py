"""Board columns (legacy ``GET /api/columns``).

Order is load-bearing (column render order), and this endpoint doubles as the
frontend TokenGate's auth probe: 200 under a valid token, 401 otherwise.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import ParadigmClaims
from ..deps import require_claims
from ..models import STATUS_IDS, STATUS_LABELS

router = APIRouter()


@router.get("/api/columns")
def list_columns(claims: ParadigmClaims = Depends(require_claims)) -> dict:
    return {"columns": [{"id": sid, "label": STATUS_LABELS[sid]} for sid in STATUS_IDS]}
