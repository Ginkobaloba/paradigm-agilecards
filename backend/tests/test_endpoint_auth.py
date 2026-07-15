"""AC-CARDS-006 -- bearer JWT verification on every authed endpoint.

The contract the chunk pins: no token -> 401, valid token -> 200, tampered
token -> 401. The public health probe stays open.
"""

from __future__ import annotations


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_healthz_is_public(client) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_no_token_is_401(client) -> None:
    resp = client.get("/api/cards")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


def test_garbage_bearer_is_401(client) -> None:
    resp = client.get("/api/cards", headers=_bearer("not-a-jwt"))
    assert resp.status_code == 401


def test_valid_token_is_200(client, make_token) -> None:
    resp = client.get("/api/cards", headers=_bearer(make_token()))
    assert resp.status_code == 200


def test_tampered_token_is_401(client, make_token) -> None:
    head_a, payload_a, _ = make_token(sub="user_a").split(".")
    _, _, sig_b = make_token(sub="user_b").split(".")
    resp = client.get("/api/cards", headers=_bearer(f"{head_a}.{payload_a}.{sig_b}"))
    assert resp.status_code == 401


def test_expired_token_is_401(client, make_token) -> None:
    resp = client.get("/api/cards", headers=_bearer(make_token(exp_offset=-10)))
    assert resp.status_code == 401


def test_every_authed_route_rejects_missing_token(client) -> None:
    # Guard against a route shipping without the dependency. Every /api/* route
    # except none-here must 401 unauthenticated.
    routes = [
        ("get", "/api/cards"),
        ("get", "/api/cards/a1"),
        ("get", "/api/me"),
        ("post", "/api/cards"),
    ]
    for method, path in routes:
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{method.upper()} {path} did not 401 without a token"
