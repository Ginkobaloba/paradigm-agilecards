"""Saved views wire contract: per-user CRUD, exact legacy error strings,
duplicate 409, and cross-org / cross-subject invisibility (RLS + owner_sub)."""

from __future__ import annotations

from conftest import ORG_A, ORG_B
from sqlalchemy import select

NAME_ERROR = "name must be a non-empty string of at most 80 characters"
PAYLOAD_ERROR = "payload too large (max 16384 bytes)"
DUPLICATE_ERROR = "a view with that name already exists"


def _create(client, headers, name="Sprint focus", payload=None):
    body = {"name": name, "payload": payload if payload is not None else {"filter": "active"}}
    return client.post("/api/views", headers=headers, json=body)


# --------------------------------------------------------------------------
# List + create
# --------------------------------------------------------------------------


def test_list_views_empty(client, auth_headers):
    res = client.get("/api/views", headers=auth_headers())
    assert res.status_code == 200
    assert res.json() == {"views": []}


def test_create_view_shape(client, auth_headers):
    res = _create(client, auth_headers(), name="  Sprint focus  ", payload={"cols": [1, 2]})
    assert res.status_code == 201
    view = res.json()
    assert set(view.keys()) == {"id", "tokenId", "name", "payload", "createdAt", "updatedAt"}
    assert isinstance(view["id"], int)
    assert view["tokenId"] == 0  # compat constant under JWKS
    assert view["name"] == "Sprint focus"  # trimmed
    assert view["payload"] == {"cols": [1, 2]}
    assert isinstance(view["createdAt"], str)
    assert isinstance(view["updatedAt"], str)


def test_create_view_audited(client, auth_headers, org_session):
    from cards_api.models import AuditEvent

    res = _create(client, auth_headers())
    assert res.status_code == 201
    with org_session(ORG_A) as s:
        rows = s.execute(select(AuditEvent).where(AuditEvent.action == "view.created")).scalars()
        events = list(rows)
    assert len(events) == 1
    assert events[0].resource_id == str(res.json()["id"])


def test_create_view_name_validation(client, auth_headers):
    headers = auth_headers()
    for body in (
        {},
        {"name": ""},
        {"name": "   "},
        {"name": 42},
        {"name": None},
        {"name": "x" * 81},
    ):
        res = client.post("/api/views", headers=headers, json=body)
        assert res.status_code == 400, body
        assert res.json() == {"error": NAME_ERROR}
    # 80 chars exactly is allowed
    res = client.post("/api/views", headers=headers, json={"name": "x" * 80, "payload": {}})
    assert res.status_code == 201


def test_create_view_payload_too_large(client, auth_headers):
    res = _create(client, auth_headers(), payload={"blob": "x" * 17000})
    assert res.status_code == 400
    assert res.json() == {"error": PAYLOAD_ERROR}


def test_create_view_duplicate_name_409(client, auth_headers):
    headers = auth_headers()
    assert _create(client, headers, name="Focus").status_code == 201
    res = _create(client, headers, name="Focus")
    assert res.status_code == 409
    assert res.json() == {"error": DUPLICATE_ERROR}
    # Trimmed collision counts too
    res = _create(client, headers, name="  Focus  ")
    assert res.status_code == 409


def test_list_views_ordered_by_name(client, auth_headers):
    headers = auth_headers()
    for name in ("bravo", "alpha", "charlie"):
        assert _create(client, headers, name=name).status_code == 201
    res = client.get("/api/views", headers=headers)
    assert [v["name"] for v in res.json()["views"]] == ["alpha", "bravo", "charlie"]


def test_list_views_scoped_to_subject(client, auth_headers):
    assert _create(client, auth_headers(sub="user_1"), name="mine").status_code == 201
    assert _create(client, auth_headers(sub="user_2"), name="theirs").status_code == 201
    res = client.get("/api/views", headers=auth_headers(sub="user_1"))
    assert [v["name"] for v in res.json()["views"]] == ["mine"]


# --------------------------------------------------------------------------
# Get by id
# --------------------------------------------------------------------------


def test_get_view_by_id(client, auth_headers):
    headers = auth_headers()
    created = _create(client, headers).json()
    res = client.get(f"/api/views/{created['id']}", headers=headers)
    assert res.status_code == 200
    assert res.json() == created


def test_get_view_bad_id(client, auth_headers):
    res = client.get("/api/views/abc", headers=auth_headers())
    assert res.status_code == 400
    assert res.json() == {"error": "bad id"}


def test_get_view_not_found(client, auth_headers):
    res = client.get("/api/views/999", headers=auth_headers())
    assert res.status_code == 404
    assert res.json() == {"error": "no such view"}


def test_get_view_foreign_subject_404(client, auth_headers):
    created = _create(client, auth_headers(sub="user_1")).json()
    res = client.get(f"/api/views/{created['id']}", headers=auth_headers(sub="user_2"))
    assert res.status_code == 404
    assert res.json() == {"error": "no such view"}


# --------------------------------------------------------------------------
# Patch
# --------------------------------------------------------------------------


def test_patch_view_name_and_payload(client, auth_headers):
    headers = auth_headers()
    created = _create(client, headers, name="old", payload={"a": 1}).json()
    res = client.patch(
        f"/api/views/{created['id']}",
        headers=headers,
        json={"name": "  new  ", "payload": {"b": 2}},
    )
    assert res.status_code == 200
    view = res.json()
    assert view["name"] == "new"
    assert view["payload"] == {"b": 2}
    assert view["updatedAt"] != created["updatedAt"]  # onupdate bumped
    assert view["createdAt"] == created["createdAt"]


def test_patch_view_partial_name_only(client, auth_headers):
    headers = auth_headers()
    created = _create(client, headers, name="old", payload={"keep": True}).json()
    res = client.patch(f"/api/views/{created['id']}", headers=headers, json={"name": "new"})
    assert res.status_code == 200
    assert res.json()["name"] == "new"
    assert res.json()["payload"] == {"keep": True}


def test_patch_view_empty_patch(client, auth_headers):
    headers = auth_headers()
    created = _create(client, headers).json()
    res = client.patch(f"/api/views/{created['id']}", headers=headers, json={"other": 1})
    assert res.status_code == 400
    assert res.json() == {"error": "empty patch"}


def test_patch_view_validation_errors(client, auth_headers):
    headers = auth_headers()
    view_id = _create(client, headers).json()["id"]
    res = client.patch(f"/api/views/{view_id}", headers=headers, json={"name": "  "})
    assert res.status_code == 400
    assert res.json() == {"error": NAME_ERROR}
    res = client.patch(
        f"/api/views/{view_id}", headers=headers, json={"payload": {"blob": "x" * 17000}}
    )
    assert res.status_code == 400
    assert res.json() == {"error": PAYLOAD_ERROR}


def test_patch_view_bad_id_and_not_found(client, auth_headers):
    headers = auth_headers()
    res = client.patch("/api/views/nope", headers=headers, json={"name": "x"})
    assert res.status_code == 400
    assert res.json() == {"error": "bad id"}
    res = client.patch("/api/views/999", headers=headers, json={"name": "x"})
    assert res.status_code == 404
    assert res.json() == {"error": "no such view"}


def test_patch_view_rename_collision_409(client, auth_headers):
    headers = auth_headers()
    assert _create(client, headers, name="taken").status_code == 201
    other = _create(client, headers, name="other").json()
    res = client.patch(f"/api/views/{other['id']}", headers=headers, json={"name": "taken"})
    assert res.status_code == 409
    assert res.json() == {"error": DUPLICATE_ERROR}


def test_patch_view_foreign_subject_404(client, auth_headers):
    created = _create(client, auth_headers(sub="user_1")).json()
    res = client.patch(
        f"/api/views/{created['id']}", headers=auth_headers(sub="user_2"), json={"name": "steal"}
    )
    assert res.status_code == 404


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------


def test_delete_view(client, auth_headers):
    headers = auth_headers()
    view_id = _create(client, headers).json()["id"]
    res = client.delete(f"/api/views/{view_id}", headers=headers)
    assert res.status_code == 204
    assert res.content == b""
    assert client.get(f"/api/views/{view_id}", headers=headers).status_code == 404


def test_delete_view_not_found(client, auth_headers):
    res = client.delete("/api/views/999", headers=auth_headers())
    assert res.status_code == 404
    assert res.json() == {"error": "no such view"}


def test_delete_view_foreign_subject_404(client, auth_headers):
    created = _create(client, auth_headers(sub="user_1")).json()
    res = client.delete(f"/api/views/{created['id']}", headers=auth_headers(sub="user_2"))
    assert res.status_code == 404


# --------------------------------------------------------------------------
# Cross-org isolation (RLS)
# --------------------------------------------------------------------------


def test_views_cross_org_isolation(client, auth_headers):
    # Same subject in both orgs: RLS alone must hide the ORG_A view from ORG_B.
    a_headers = auth_headers(org_id=ORG_A, sub="user_1")
    b_headers = auth_headers(org_id=ORG_B, sub="user_1")
    created = _create(client, a_headers, name="secret").json()

    assert client.get("/api/views", headers=b_headers).json() == {"views": []}
    assert client.get(f"/api/views/{created['id']}", headers=b_headers).status_code == 404
    res = client.patch(f"/api/views/{created['id']}", headers=b_headers, json={"name": "x"})
    assert res.status_code == 404
    assert client.delete(f"/api/views/{created['id']}", headers=b_headers).status_code == 404
    # And the ORG_A view survived untouched.
    res = client.get(f"/api/views/{created['id']}", headers=a_headers)
    assert res.status_code == 200
    assert res.json()["name"] == "secret"
