"""Liveness probe contract (AC-OBS-004; audit M3/T5).

The smoke gate asserts ``$.ok == true``, so that key is load-bearing. With no
CARDS_DATABASE_URL configured (this test's environment), the app must still
boot and report the database as unconfigured rather than refusing to start —
liveness and dependency health are separate signals.
"""

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_healthz_returns_200_ok() -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["db"] == "unconfigured"
    assert isinstance(body["version"], str)
