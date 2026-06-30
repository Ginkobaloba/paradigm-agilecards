"""AC-CARDS-007 -- org_id + roles from claims, authorization + isolation.

org_id and roles are read from the verified token (never from the request body
or query) and drive: (1) multi-org data isolation -- a caller only ever sees its
own org's cards, and (2) role-based authorization on mutating routes.
"""

from __future__ import annotations

from conftest import ORG_A, ORG_B


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_claims_are_extracted_from_token(client, make_token) -> None:
    token = make_token(org_id=ORG_A, roles=["admin", "member"], sub="user_777")
    resp = client.get("/api/me", headers=_bearer(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["org_id"] == ORG_A
    assert body["sub"] == "user_777"
    assert body["roles"] == ["admin", "member"]


def test_org_a_sees_only_org_a_cards(client, make_token) -> None:
    resp = client.get("/api/cards", headers=_bearer(make_token(org_id=ORG_A)))
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()["cards"]}
    assert ids == {"a1", "a2"}


def test_org_b_sees_only_org_b_cards(client, make_token) -> None:
    resp = client.get("/api/cards", headers=_bearer(make_token(org_id=ORG_B)))
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()["cards"]}
    assert ids == {"b1"}


def test_cross_org_card_read_is_404(client, make_token) -> None:
    # Org B asking for Org A's card must not even learn it exists.
    resp = client.get("/api/cards/a1", headers=_bearer(make_token(org_id=ORG_B)))
    assert resp.status_code == 404


def test_same_org_card_read_is_200(client, make_token) -> None:
    resp = client.get("/api/cards/a1", headers=_bearer(make_token(org_id=ORG_A)))
    assert resp.status_code == 200
    assert resp.json()["id"] == "a1"


def test_member_cannot_create_card(client, make_token) -> None:
    token = make_token(org_id=ORG_A, roles=["member"])
    resp = client.post("/api/cards", headers=_bearer(token), json={"title": "new"})
    assert resp.status_code == 403


def test_admin_can_create_card_scoped_to_its_org(client, make_token) -> None:
    admin = make_token(org_id=ORG_B, roles=["admin", "member"])
    resp = client.post("/api/cards", headers=_bearer(admin), json={"title": "Globex: new"})
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    assert resp.json()["org_id"] == ORG_B

    # Visible to its own org...
    org_b_list = client.get("/api/cards", headers=_bearer(make_token(org_id=ORG_B)))
    assert new_id in {c["id"] for c in org_b_list.json()["cards"]}

    # ...and invisible to the other org.
    org_a_list = client.get("/api/cards", headers=_bearer(make_token(org_id=ORG_A)))
    assert new_id not in {c["id"] for c in org_a_list.json()["cards"]}


def test_created_card_uses_token_org_not_body(client, make_token) -> None:
    # Even if the caller tries to inject a foreign org_id in the body, the
    # verified token's org_id wins.
    admin = make_token(org_id=ORG_A, roles=["admin"])
    resp = client.post(
        "/api/cards",
        headers=_bearer(admin),
        json={"title": "sneaky", "org_id": ORG_B},
    )
    assert resp.status_code == 201
    assert resp.json()["org_id"] == ORG_A
