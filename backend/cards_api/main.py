"""FastAPI application for the Paradigm AgileCards backend (chunk K11).

Wires the JWKS verifier (AC-CARDS-003) into a bearer guard on every authed
route (AC-CARDS-006), enforces org isolation + role authorization from verified
claims (AC-CARDS-007), and sources its config/secrets at boot (AC-CARDS-008).

The card surface here is intentionally narrow -- enough to prove the auth and
isolation contract. The full card CRUD rewrite of the legacy Express backend is
a separate chunk.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel

from .auth import ParadigmClaims, TokenVerifier
from .config import load_settings
from .deps import get_store, require_claims, require_roles
from .store import CardStore, default_store


class CardCreate(BaseModel):
    title: str
    # An org_id sent in the body is ignored on purpose: the verified token's
    # org_id is authoritative (see create_card). Declared so it is accepted but
    # never trusted.
    org_id: str | None = None


def create_app(
    *,
    verifier: TokenVerifier | None = None,
    store: CardStore | None = None,
) -> FastAPI:
    """Application factory. Tests inject an offline verifier and a seeded store;
    production builds both from settings resolved at boot."""
    settings = load_settings()
    app = FastAPI(title="Paradigm AgileCards API", version="1.0.0")
    app.state.verifier = verifier or TokenVerifier(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        jwks_url=settings.jwks_url,
    )
    app.state.store = store if store is not None else default_store()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Unauthenticated liveness probe (AC-OBS-004)."""
        return {"status": "ok"}

    @app.get("/api/me")
    def whoami(claims: ParadigmClaims = Depends(require_claims)) -> dict:
        """Echo the identity extracted from the verified token (AC-CARDS-007)."""
        return {"sub": claims.sub, "org_id": claims.org_id, "roles": list(claims.roles)}

    @app.get("/api/cards")
    def list_cards(
        claims: ParadigmClaims = Depends(require_claims),
        store: CardStore = Depends(get_store),
    ) -> dict:
        cards = [c.public_dict() for c in store.list_for_org(claims.org_id)]
        return {"org_id": claims.org_id, "cards": cards}

    @app.get("/api/cards/{card_id}")
    def get_card(
        card_id: str,
        claims: ParadigmClaims = Depends(require_claims),
        store: CardStore = Depends(get_store),
    ) -> dict:
        card = store.get_for_org(card_id, claims.org_id)
        if card is None:
            # 404 (not 403) so a caller cannot probe another org's card ids.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail={"error": "not_found"}
            )
        return card.public_dict()

    @app.post("/api/cards", status_code=201)
    def create_card(
        body: CardCreate,
        claims: ParadigmClaims = Depends(require_roles("admin")),
        store: CardStore = Depends(get_store),
    ) -> dict:
        # org_id is taken from the verified token, never from the request body.
        card = store.create(org_id=claims.org_id, title=body.title)
        return card.public_dict()

    return app


app = create_app()
