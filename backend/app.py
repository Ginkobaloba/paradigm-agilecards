"""Entrypoint shim for the Paradigm AgileCards backend.

The real application lives in the ``cards_api`` package (chunk K11): direct
JWKS JWT verification (AC-CARDS-003), a bearer guard on every authed endpoint
(AC-CARDS-006), org_id/roles isolation and authorization (AC-CARDS-007), and
Infisical-sourced secrets at boot (AC-CARDS-008).

This module exists so ``uvicorn app:app`` and the original import path keep
working.

Run locally:  uvicorn app:app --reload
"""

from __future__ import annotations

from cards_api import app

__all__ = ["app"]
