"""Columns, rates, identity — the small read-only surfaces."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import ParadigmClaims
from ..deps import require_claims
from ..models import STATUS_IDS, STATUS_LABELS
from ..rates import rates_payload

router = APIRouter()


@router.get("/api/columns")
def list_columns(claims: ParadigmClaims = Depends(require_claims)) -> dict:
    return {
        "columns": [{"id": status, "label": STATUS_LABELS[status]} for status in STATUS_IDS]
    }


@router.get("/api/rates")
def list_rates(claims: ParadigmClaims = Depends(require_claims)) -> dict:
    return rates_payload()


@router.get("/api/me")
def whoami(claims: ParadigmClaims = Depends(require_claims)) -> dict:
    """Echo the identity extracted from the verified token (AC-CARDS-007)."""
    return {"sub": claims.sub, "org_id": claims.org_id, "roles": list(claims.roles)}
