"""Smoke test for the entrypoint shim: ``uvicorn app:app`` must keep working.

The app boots with no database configured (PARADIGM_DATABASE_URL unset in unit
CI); the health probe stays green and DB-backed routes 503 cleanly instead of
crashing -- that behavior is covered in test_endpoint_auth.
"""

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_healthz_returns_200_ok() -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "version" in body
    assert "cardsDir" in body  # legacy-typed field, vestigial but load-bearing
