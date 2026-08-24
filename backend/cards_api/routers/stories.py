"""Stories routes — deliberately NOT ported (ADR D8, contract deviation #6).

The legacy implementation shells out to the `claude` CLI from the web
process. That execution path belongs behind the engine runner (audit P2:
one execution engine, one verifier, one ledger); porting the CLI spawn here
would cement the wrong architecture. Until the P2 arc lands, these answer
501 so the contract surface is explicit rather than absent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import ParadigmClaims
from ..deps import require_claims
from .common import err

router = APIRouter()

_MESSAGE = (
    "stories execution is not implemented in this backend; it is owned by "
    "the engine-runner integration (see docs/adr/ADR-2026-07-16-cards-api-"
    "postgres-rls.md, D8)"
)


@router.post("/api/stories/submit")
def submit_story(claims: ParadigmClaims = Depends(require_claims)) -> dict:
    raise err(501, _MESSAGE)


@router.post("/api/stories/{batch_id}/approve")
def approve_story(
    batch_id: str, claims: ParadigmClaims = Depends(require_claims)
) -> dict:
    raise err(501, _MESSAGE)


@router.post("/api/stories/{batch_id}/cancel")
def cancel_story(
    batch_id: str, claims: ParadigmClaims = Depends(require_claims)
) -> dict:
    raise err(501, _MESSAGE)
