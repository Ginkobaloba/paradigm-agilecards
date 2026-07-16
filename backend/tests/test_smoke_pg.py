"""End-to-end smoke: migrated Postgres + app role + one authed round trip."""

from __future__ import annotations


def test_healthz_shape(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert isinstance(body["cardsDir"], str)
    assert isinstance(body["version"], str)


def test_columns_roundtrip(client, auth_headers):
    res = client.get("/api/columns", headers=auth_headers())
    assert res.status_code == 200
    columns = res.json()["columns"]
    assert [c["id"] for c in columns] == [
        "backlog",
        "active",
        "awaiting_amendment_review",
        "done",
        "blocked",
    ]
    assert columns[2]["label"] == "In Review"


def test_columns_rejects_bad_token(client):
    res = client.get("/api/columns", headers={"Authorization": "Bearer nonsense"})
    assert res.status_code == 401
    assert res.json()["error"] == "invalid_token"
