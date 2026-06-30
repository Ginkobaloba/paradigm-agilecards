"""Smoke test for the K2 backend scaffold.

This is the passing CI placeholder. Substantive backend tests (JWKS verify,
401 on missing/tampered tokens, org isolation) arrive with chunk K11.
"""

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_healthz_returns_200_ok() -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
