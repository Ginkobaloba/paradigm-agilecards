"""AC-CARDS-006 over the real API: every route guarded, contract error
shapes (top-level {"error": ...}), healthz public. Supersedes the in-memory
test_endpoint_auth.py from K11 — same semantics, real database behind it."""

from __future__ import annotations

from fastapi.routing import APIRoute


def test_healthz_is_public_and_reports_db(client) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["db"] == "ok"
    assert isinstance(body["version"], str)


def test_no_token_is_401_with_contract_shape(client) -> None:
    resp = client.get("/api/cards")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"
    assert resp.json() == {"error": "missing_token"}


def test_garbage_bearer_is_401(client) -> None:
    resp = client.get("/api/cards", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_tampered_token_is_401(client, make_token) -> None:
    head_a, payload_a, _ = make_token(sub="user_a").split(".")
    _, _, sig_b = make_token(sub="user_b").split(".")
    resp = client.get(
        "/api/cards", headers={"Authorization": f"Bearer {head_a}.{payload_a}.{sig_b}"}
    )
    assert resp.status_code == 401


def test_expired_token_is_401(client, make_token, bearer) -> None:
    resp = client.get("/api/cards", headers=bearer(exp_offset=-10))
    assert resp.status_code == 401


def test_valid_token_is_200(client, bearer) -> None:
    resp = client.get("/api/cards", headers=bearer())
    assert resp.status_code == 200


def test_every_route_rejects_missing_token(app, client) -> None:
    """Structural guard: EVERY route except /healthz must 401 without a
    token — a new route shipping without the auth dependency fails here."""
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path == "/healthz":
            continue
        path = route.path
        for param, dummy in (
            ("{card_id}", "x1"),
            ("{view_id}", "1"),
            ("{sprint_id}", "1"),
            ("{retro_id}", "1"),
            ("{batch_id}", "b1"),
            ("{file}", "c.md"),
        ):
            path = path.replace(param, dummy)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            resp = client.request(method, path)
            assert resp.status_code == 401, (
                f"{method} {path} answered {resp.status_code} without a token"
            )


def test_member_cannot_create_card(client, bearer) -> None:
    resp = client.post(
        "/api/cards", headers=bearer(roles=["member"]), json={"title": "new"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "insufficient_role"


def test_member_cannot_ingest_triage_or_read_audit(client, bearer) -> None:
    member = bearer(roles=["member"])
    resp = client.post(
        "/api/triage/batches",
        headers=member,
        json={"batchId": "b1", "cards": [{"file": "a.md"}]},
    )
    assert resp.status_code == 403
    assert client.get("/api/audit", headers=member).status_code == 403
