"""Model rates (legacy ``GET /api/rates``): static pricing for cost chips."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import ParadigmClaims
from ..deps import require_claims
from ..rates import DEFAULT_INPUT_RATIO, MODEL_RATES

router = APIRouter()


@router.get("/api/rates")
def list_rates(claims: ParadigmClaims = Depends(require_claims)) -> dict:
    return {"rates": list(MODEL_RATES), "defaultInputRatio": DEFAULT_INPUT_RATIO}
