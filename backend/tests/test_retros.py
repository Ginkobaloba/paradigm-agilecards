"""Retros wire contract: snake_case shapes (deliberately unlike sprints),
create returns a bare {"id"}, get returns the unwrapped row, and RLS hides
foreign-org retros."""

from __future__ import annotations

from conftest import ORG_A, ORG_B
from sqlalchemy import select

RETRO_KEYS = {"id", "sprint_id", "held_on", "summary", "created_at"}


def _create(client, headers, held_on="2026-07-15", **extra):
    return client.post("/api/retros", headers=headers, json={"heldOn": held_on, **extra})


def test_list_retros_empty(client, auth_headers):
    res = client.get("/api/retros", headers=auth_headers())
    assert res.status_code == 200
    assert res.json() == {"retros": []}


def test_create_retro_returns_bare_id(client, auth_headers):
    res = _create(client, auth_headers(), sprintId=7, summary="went fine")
    assert res.status_code == 201
    body = res.json()
    assert set(body.keys()) == {"id"}  # legacy returns only the new id
    assert isinstance(body["id"], int)


def test_create_retro_audited(client, auth_headers, org_session):
    from cards_api.models import AuditEvent

    res = _create(client, auth_headers())
    assert res.status_code == 201
    with org_session(ORG_A) as s:
        events = list(
            s.execute(select(AuditEvent).where(AuditEvent.action == "retro.created")).scalars()
        )
    assert len(events) == 1
    assert events[0].resource_id == str(res.json()["id"])


def test_create_retro_requires_held_on(client, auth_headers):
    headers = auth_headers()
    for body in ({}, {"heldOn": ""}, {"heldOn": "   "}, {"heldOn": 20260715}):
        res = client.post("/api/retros", headers=headers, json=body)
        assert res.status_code == 400, body
        assert res.json() == {"error": "heldOn is required"}


def test_create_retro_coercions(client, auth_headers):
    headers = auth_headers()
    # sprintId: numbers coerce via int(); non-numeric -> null. summary: string or null.
    cases = [
        ({"sprintId": 5, "summary": "s"}, 5, "s"),
        ({"sprintId": 5.7}, 5, None),
        ({"sprintId": "abc", "summary": 42}, None, None),
        ({}, None, None),
    ]
    for extra, want_sprint_id, want_summary in cases:
        retro_id = _create(client, headers, **extra).json()["id"]
        retro = client.get(f"/api/retros/{retro_id}", headers=headers).json()
        assert retro["sprint_id"] == want_sprint_id, extra
        assert retro["summary"] == want_summary, extra


def test_get_retro_snake_case_unwrapped(client, auth_headers):
    headers = auth_headers()
    retro_id = _create(client, headers, held_on="2026-07-10", summary="ok").json()["id"]
    res = client.get(f"/api/retros/{retro_id}", headers=headers)
    assert res.status_code == 200
    retro = res.json()  # the row itself, not {"retro": ...}
    assert set(retro.keys()) == RETRO_KEYS
    assert retro["id"] == retro_id
    assert retro["held_on"] == "2026-07-10"
    assert retro["summary"] == "ok"
    assert retro["sprint_id"] is None
    assert isinstance(retro["created_at"], str)


def test_get_retro_bad_id(client, auth_headers):
    res = client.get("/api/retros/abc", headers=auth_headers())
    assert res.status_code == 400
    assert res.json() == {"error": "bad id"}


def test_get_retro_not_found(client, auth_headers):
    res = client.get("/api/retros/999", headers=auth_headers())
    assert res.status_code == 404
    assert res.json() == {"error": "no such retro"}


def test_list_retros_ordered_by_id(client, auth_headers):
    headers = auth_headers()
    ids = [_create(client, headers, held_on=f"2026-07-{d:02d}").json()["id"] for d in (3, 1, 2)]
    res = client.get("/api/retros", headers=headers)
    retros = res.json()["retros"]
    assert [r["id"] for r in retros] == sorted(ids)  # id ASC, not held_on
    assert set(retros[0].keys()) == RETRO_KEYS


def test_retros_cross_org_isolation(client, auth_headers):
    a_headers = auth_headers(org_id=ORG_A)
    b_headers = auth_headers(org_id=ORG_B)
    retro_id = _create(client, a_headers, summary="acme only").json()["id"]

    assert client.get("/api/retros", headers=b_headers).json() == {"retros": []}
    assert client.get(f"/api/retros/{retro_id}", headers=b_headers).status_code == 404
    # Still visible to ORG_A
    res = client.get(f"/api/retros/{retro_id}", headers=a_headers)
    assert res.status_code == 200
    assert res.json()["summary"] == "acme only"
