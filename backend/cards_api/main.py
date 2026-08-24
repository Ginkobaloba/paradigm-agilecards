"""FastAPI application for the Paradigm AgileCards backend.

K11 delivered the auth spine (JWKS verify, org isolation from verified
claims, Infisical config). This module now wires the real product API on top
of it: Postgres persistence with database-enforced RLS, the full board CRUD
contract (docs/board/CARDS_API_CONTRACT.md), org-scoped SSE, and the audit
seam. Architecture decisions: docs/adr/ADR-2026-07-16-cards-api-postgres-rls.md.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .audit import configure_logging
from .auth import TokenVerifier
from .bus import OrgEventBus
from .config import load_settings
from .db import make_engine, make_session_factory
from .routers import ALL_ROUTERS

API_VERSION = "1.1.0"


def create_app(
    *,
    verifier: TokenVerifier | None = None,
    database_url: str | None = None,
) -> FastAPI:
    """Application factory. Tests inject an offline verifier and a test
    database URL; production builds both from settings resolved at boot."""
    settings = load_settings()
    configure_logging()

    db_url = database_url or settings.database_url
    engine = make_engine(db_url) if db_url else None
    session_factory = make_session_factory(engine) if engine is not None else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # SSE consumers live on this loop; sync routes publish onto it.
        app.state.bus.attach_loop(asyncio.get_running_loop())
        yield
        if engine is not None:
            engine.dispose()

    app = FastAPI(title="Paradigm AgileCards API", version=API_VERSION, lifespan=lifespan)
    app.state.verifier = verifier or TokenVerifier(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        jwks_url=settings.jwks_url,
    )
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.bus = OrgEventBus()

    @app.exception_handler(HTTPException)
    async def contract_error_shape(request: Request, exc: HTTPException) -> JSONResponse:
        """The board contract is a TOP-LEVEL ``{"error": ...}`` body, not
        FastAPI's default ``{"detail": ...}`` envelope."""
        content = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def body_validation_shape(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "body must be JSON"})

    @app.get("/healthz")
    def healthz() -> dict:
        """Unauthenticated liveness probe (AC-OBS-004). ``ok`` means the
        process is up; ``db`` is a best-effort connectivity report. The
        smoke gate asserts ``$.ok == true`` (audit M3)."""
        if engine is None:
            db_state = "unconfigured"
        else:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                db_state = "ok"
            except Exception:  # noqa: BLE001 - health probe must not raise
                db_state = "error"
        return {"ok": True, "version": API_VERSION, "db": db_state}

    for router in ALL_ROUTERS:
        app.include_router(router)

    return app


app = create_app()
