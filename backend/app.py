"""Paradigm AgileCards backend (FastAPI).

K2 ships only this scaffold: the folder structure plus a health endpoint that
makes CI meaningful. The real Cards API -- direct JWKS JWT verification
(PyJWT/cryptography), org_id/roles authorization and isolation, and
Infisical-sourced secrets at boot -- is owned by chunk K11
(AC-CARDS-003/006/007/008). Do not build that here.

Run locally:  uvicorn app:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Paradigm AgileCards API", version="0.0.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe.

    K10 wires this under ``cards.paradigm.codes/api/`` on deploy (AC-CARDS-005).
    """
    return {"status": "ok"}
