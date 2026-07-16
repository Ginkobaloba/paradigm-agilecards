"""FastAPI application for the Paradigm AgileCards backend.

K11 delivered the auth contract (JWKS verify, org isolation, secrets at boot).
This revision delivers the rest of the backend for real (ADR-2026-07-16):
Postgres persistence with database-enforced row-level security, the full board
CRUD surface at legacy-Express wire parity, an append-only audit trail, and the
SSE live channel.

Error envelope: the frontend reads a top-level ``error`` key from failure
bodies (legacy contract). FastAPI nests ``HTTPException.detail`` under
``detail`` by default, so a global handler flattens dict details to the top
level. Validation errors surface as 400 ``{"error": ...}`` for the same reason.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .audit import AuditLog
from .auth import ParadigmClaims, TokenVerifier
from .config import Settings, load_settings
from .db import Database
from .deps import require_claims
from .events import EventBus
from .routers import audit_admin, cards, columns, health, ranks, rates_router, sse
from .routers import retros as retros_router
from .routers import sprints as sprints_router
from .routers import stories as stories_router
from .routers import triage as triage_router
from .routers import views as views_router


def _configure_logging() -> None:
    """Structured-ish stdout logging (audit item S4). Uvicorn owns access logs;
    this covers application + audit records with stable, parseable fields."""
    root = logging.getLogger()
    if root.handlers:  # respect an embedding process's configuration
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def create_app(
    *,
    verifier: TokenVerifier | None = None,
    database: Database | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Application factory. Tests inject an offline verifier and a migrated
    test Database; production builds both from settings resolved at boot."""
    _configure_logging()
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if app.state.db is not None:
            app.state.db.dispose()

    app = FastAPI(title="Paradigm AgileCards API", version="1.0.0", lifespan=lifespan)

    app.state.verifier = verifier or TokenVerifier(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        jwks_url=settings.jwks_url,
    )
    if database is not None:
        app.state.db = database
    elif settings.database_url:
        app.state.db = Database(settings.database_url)
    else:
        app.state.db = None
    app.state.bus = EventBus()
    app.state.audit = AuditLog(app.state.db)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,  # bearer tokens, no cookies
            allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.exception_handler(HTTPException)
    async def flatten_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        """Serve `{"error": ...}` at the top level (legacy contract)."""
        body = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def flatten_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        message = first.get("msg", "invalid request")
        return JSONResponse(status_code=400, content={"error": message})

    @app.get("/api/me")
    def whoami(claims: ParadigmClaims = Depends(require_claims)) -> dict:
        """Echo the identity extracted from the verified token (AC-CARDS-007)."""
        return {"sub": claims.sub, "org_id": claims.org_id, "roles": list(claims.roles)}

    app.include_router(health.router)
    app.include_router(columns.router)
    app.include_router(cards.router)
    app.include_router(ranks.router)
    app.include_router(rates_router.router)
    app.include_router(views_router.router)
    app.include_router(sprints_router.router)
    app.include_router(retros_router.router)
    app.include_router(triage_router.router)
    app.include_router(stories_router.router)
    app.include_router(sse.router)
    app.include_router(audit_admin.router)

    return app


app = create_app()
