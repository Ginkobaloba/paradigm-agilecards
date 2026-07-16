"""Liveness and readiness probes.

``/healthz`` is the legacy-shaped liveness probe: ``{ok, cardsDir, version}``
(``cardsDir`` is vestigial -- cards live in Postgres now -- but the field stays
for the typed frontend surface and the smoke gate's ``$.ok == true`` assert).
``/readyz`` additionally proves database connectivity for deploy gates.
Both are unauthenticated (AC-OBS-004).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter()

try:
    APP_VERSION = version("paradigm-agilecards-backend")
except PackageNotFoundError:  # running from a plain checkout without install
    APP_VERSION = "0.0.0-dev"


@router.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "cardsDir": "", "version": APP_VERSION}


@router.get("/readyz")
def readyz(request: Request) -> JSONResponse:
    db = request.app.state.db
    if db is None:
        return JSONResponse(
            status_code=503, content={"ok": False, "error": "database_not_configured"}
        )
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any connectivity failure means not ready
        return JSONResponse(
            status_code=503, content={"ok": False, "error": "database_unavailable"}
        )
    return JSONResponse(content={"ok": True, "version": APP_VERSION})
