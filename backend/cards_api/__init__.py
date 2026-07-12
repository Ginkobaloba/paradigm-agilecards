"""Paradigm AgileCards backend package (chunk K11).

Owns AC-CARDS-003 (JWKS JWT verification), AC-CARDS-006 (bearer guard on every
authed endpoint), AC-CARDS-007 (org_id/roles extraction + isolation), and
AC-CARDS-008 (secrets from Infisical at boot).
"""

from __future__ import annotations

__all__ = ["app"]


def __getattr__(name: str):
    # Lazy so importing a single submodule (e.g. cards_api.auth in a unit test)
    # doesn't pull in the whole FastAPI app graph.
    if name == "app":
        from .main import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
