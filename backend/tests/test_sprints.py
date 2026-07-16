"""Sprints wire contract: camelCase shapes, TEXT-date string semantics, exact
legacy error strings, rollups, archive filtering, card-link upsert semantics,
and cross-org invisibility under RLS."""

from __future__ import annotations

from conftest import ORG_A, ORG_B
from sqlalchemy import select

SPRINT_KEYS = {
    "id",
    "name",
    "startsAt",
    "endsAt",
    "goal",
    "status",
    "pointsTarget",
    "dollarTarget",
    "reviewHoursTarget",
    "archivedAt",
    "createdAt",
}


def _create(client, headers, name="Sprint 1", starts="2026-07-01", ends="2026-07-14", **extra):
    body = {"name": name, "startsAt": starts, "endsAt": ends, **extra}
    return client.post("/api/sprints", headers=headers, json=body)


# --------------------------------------------------------------------------
# List + create
# --------------------------------------------------------------------------


def test_list_sprints_empty(client, auth_headers):
    res = client.get("/api/sprints", headers=auth_headers())
    assert res.status_code == 200
    assert res.json() == {"sprints": []}


def test_create_sprint_shape(client, auth_headers):
    res = _create(client, auth_headers(), name="  Q3 push  ", goal="ship it")
    assert res.status_code == 201
    sprint = res.json()["sprint"]
    assert set(sprint.keys()) == SPRINT_KEYS
    assert isinstance(sprint["id"], int)
    assert sprint["name"] == "Q3 push"  # trimmed
    assert sprint["startsAt"] == "2026-07-01"
    assert sprint["endsAt"] == "2026-07-14"
    assert sprint["goal"] == "ship it"
    assert sprint["status"] == "planning"
    assert sprint["pointsTarget"] is None
    assert sprint["dollarTarget"] is None
    assert sprint["reviewHoursTarget"] is None
    assert sprint["archivedAt"] is None
    assert isinstance(sprint["createdAt"], str)


def test_create_sprint_audited(client, auth_headers, org_session):
    from cards_api.models import AuditEvent

    res = _create(client, auth_headers())
    assert res.status_code == 201
    with org_session(ORG_A) as s:
        events = list(
            s.execute(select(AuditEvent).where(AuditEvent.action == "sprint.created")).scalars()
        )
    assert len(events) == 1
    assert events[0].resource_id == str(res.json()["sprint"]["id"])


def test_create_sprint_requires_name(client, auth_headers):
    headers = auth_headers()
    for body in (
        {"startsAt": "2026-07-01", "endsAt": "2026-07-14"},
        {"name": "  ", "startsAt": "2026-07-01", "endsAt": "2026-07-14"},
        {"name": 5, "startsAt": "2026-07-01", "endsAt": "2026-07-14"},
    ):
        res = client.post("/api/sprints", headers=headers, json=body)
        assert res.status_code == 400, body
        assert res.json() == {"error": "name is required"}


def test_create_sprint_requires_dates(client, auth_headers):
    headers = auth_headers()
    for body in (
        {"name": "s"},
        {"name": "s", "startsAt": "2026-07-01"},
        {"name": "s", "endsAt": "2026-07-14"},
        {"name": "s", "startsAt": "", "endsAt": "2026-07-14"},
        {"name": "s", "startsAt": "2026-07-01", "endsAt": 7},
    ):
        res = client.post("/api/sprints", headers=headers, json=body)
        assert res.status_code == 400, body
        assert res.json() == {"error": "startsAt and endsAt are required"}


def test_create_sprint_ends_before_starts(client, auth_headers):
    res = _create(client, auth_headers(), starts="2026-07-14", ends="2026-07-01")
    assert res.status_code == 400
    assert res.json() == {"error": "endsAt cannot be before startsAt"}


def test_create_sprint_invalid_status_silently_defaults(client, auth_headers):
    # Legacy quirk: an invalid status on create is NOT an error.
    res = _create(client, auth_headers(), status="bogus")
    assert res.status_code == 201
    assert res.json()["sprint"]["status"] == "planning"


def test_create_sprint_valid_status_kept(client, auth_headers):
    res = _create(client, auth_headers(), status="active")
    assert res.status_code == 201
    assert res.json()["sprint"]["status"] == "active"


def test_list_sprints_ordering_and_rollups(client, auth_headers):
    headers = auth_headers()
    older = _create(client, headers, name="older", starts="2026-06-01", ends="2026-06-14")
    newer = _create(client, headers, name="newer", starts="2026-07-01", ends="2026-07-14")
    older_id = older.json()["sprint"]["id"]
    newer_id = newer.json()["sprint"]["id"]
    for card, points in (("c1", 3), ("c2", 5)):
        res = client.post(
            f"/api/sprints/{older_id}/cards",
            headers=headers,
            json={"cardId": card, "plannedPoints": points},
        )
        assert res.status_code == 204

    res = client.get("/api/sprints", headers=headers)
    sprints = res.json()["sprints"]
    assert [s["name"] for s in sprints] == ["newer", "older"]  # startsAt DESC
    by_id = {s["id"]: s for s in sprints}
    assert by_id[older_id]["cardCount"] == 2
    assert by_id[older_id]["plannedPointsSum"] == 8
    assert by_id[newer_id]["cardCount"] == 0
    assert by_id[newer_id]["plannedPointsSum"] == 0
    assert set(sprints[0].keys()) == SPRINT_KEYS | {"cardCount", "plannedPointsSum"}


def test_list_sprints_include_archived(client, auth_headers):
    headers = auth_headers()
    live = _create(client, headers, name="live", starts="2026-07-01", ends="2026-07-14")
    gone = _create(client, headers, name="gone", starts="2026-06-01", ends="2026-06-14")
    gone_id = gone.json()["sprint"]["id"]
    res = client.patch(
        f"/api/sprints/{gone_id}", headers=headers, json={"archivedAt": "2026-06-15"}
    )
    assert res.status_code == 200

    assert live.status_code == 201
    default = client.get("/api/sprints", headers=headers).json()["sprints"]
    assert [s["name"] for s in default] == ["live"]
    for truthy in ("1", "true", "yes", "TRUE"):
        res = client.get(f"/api/sprints?includeArchived={truthy}", headers=headers)
        assert [s["name"] for s in res.json()["sprints"]] == ["live", "gone"], truthy
    for falsy in ("0", "no", "", "nope"):
        res = client.get(f"/api/sprints?includeArchived={falsy}", headers=headers)
        assert [s["name"] for s in res.json()["sprints"]] == ["live"], falsy


# --------------------------------------------------------------------------
# Get by id
# --------------------------------------------------------------------------


def test_get_sprint_with_cards(client, auth_headers):
    headers = auth_headers()
    sprint = _create(client, headers).json()["sprint"]
    res = client.post(
        f"/api/sprints/{sprint['id']}/cards",
        headers=headers,
        json={"cardId": "card-1", "plannedPoints": 3},
    )
    assert res.status_code == 204
    res = client.get(f"/api/sprints/{sprint['id']}", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["sprint"] == sprint
    assert body["cards"] == [{"sprintId": sprint["id"], "cardId": "card-1", "plannedPoints": 3}]


def test_get_sprint_bad_id(client, auth_headers):
    res = client.get("/api/sprints/abc", headers=auth_headers())
    assert res.status_code == 400
    assert res.json() == {"error": "bad id"}


def test_get_sprint_not_found(client, auth_headers):
    res = client.get("/api/sprints/999", headers=auth_headers())
    assert res.status_code == 404
    assert res.json() == {"error": "no such sprint"}


# --------------------------------------------------------------------------
# Patch
# --------------------------------------------------------------------------


def test_patch_sprint_fields(client, auth_headers):
    headers = auth_headers()
    sprint_id = _create(client, headers).json()["sprint"]["id"]
    res = client.patch(
        f"/api/sprints/{sprint_id}",
        headers=headers,
        json={
            "name": "  renamed  ",
            "startsAt": "2026-07-02",
            "endsAt": "2026-07-15",
            "goal": "new goal",
            "status": "active",
            "pointsTarget": 21,
            "dollarTarget": 1000.5,
            "reviewHoursTarget": 12,
            "archivedAt": None,
        },
    )
    assert res.status_code == 200
    sprint = res.json()["sprint"]
    assert sprint["name"] == "renamed"
    assert sprint["startsAt"] == "2026-07-02"
    assert sprint["endsAt"] == "2026-07-15"
    assert sprint["goal"] == "new goal"
    assert sprint["status"] == "active"
    assert sprint["pointsTarget"] == 21
    assert sprint["dollarTarget"] == 1000.5
    assert sprint["reviewHoursTarget"] == 12.0
    assert sprint["archivedAt"] is None


def test_patch_sprint_numeric_coercion(client, auth_headers):
    headers = auth_headers()
    sprint_id = _create(client, headers).json()["sprint"]["id"]
    # pointsTarget floors; negatives clamp to 0
    res = client.patch(f"/api/sprints/{sprint_id}", headers=headers, json={"pointsTarget": 5.9})
    assert res.json()["sprint"]["pointsTarget"] == 5
    res = client.patch(f"/api/sprints/{sprint_id}", headers=headers, json={"pointsTarget": -3})
    assert res.json()["sprint"]["pointsTarget"] == 0
    res = client.patch(f"/api/sprints/{sprint_id}", headers=headers, json={"dollarTarget": -2})
    assert res.json()["sprint"]["dollarTarget"] == 0.0
    # None clears
    res = client.patch(
        f"/api/sprints/{sprint_id}",
        headers=headers,
        json={"pointsTarget": None, "dollarTarget": None, "reviewHoursTarget": None},
    )
    sprint = res.json()["sprint"]
    assert sprint["pointsTarget"] is None
    assert sprint["dollarTarget"] is None
    assert sprint["reviewHoursTarget"] is None


def test_patch_sprint_validation_errors(client, auth_headers):
    headers = auth_headers()
    sprint_id = _create(client, headers).json()["sprint"]["id"]
    cases = [
        ({"name": "  "}, "name must be a non-empty string"),
        ({"name": 7}, "name must be a non-empty string"),
        ({"status": "bogus"}, "invalid status"),
        ({"pointsTarget": "many"}, "pointsTarget must be a number"),
        ({"pointsTarget": True}, "pointsTarget must be a number"),
        ({"dollarTarget": "lots"}, "dollarTarget must be a number"),
        ({"reviewHoursTarget": "some"}, "reviewHoursTarget must be a number"),
    ]
    for body, error in cases:
        res = client.patch(f"/api/sprints/{sprint_id}", headers=headers, json=body)
        assert res.status_code == 400, body
        assert res.json() == {"error": error}


def test_patch_sprint_no_recognized_fields(client, auth_headers):
    headers = auth_headers()
    sprint_id = _create(client, headers).json()["sprint"]["id"]
    for body in ({}, {"bogus": 1}, {"startsAt": ""}):
        res = client.patch(f"/api/sprints/{sprint_id}", headers=headers, json=body)
        assert res.status_code == 400, body
        assert res.json() == {"error": "no recognized fields in patch"}


def test_patch_sprint_bad_id_and_not_found(client, auth_headers):
    headers = auth_headers()
    res = client.patch("/api/sprints/nope", headers=headers, json={"name": "x"})
    assert res.status_code == 400
    assert res.json() == {"error": "bad id"}
    res = client.patch("/api/sprints/999", headers=headers, json={"name": "x"})
    assert res.status_code == 404
    assert res.json() == {"error": "no such sprint"}


def test_patch_sprint_audit_lists_fields(client, auth_headers, org_session):
    from cards_api.models import AuditEvent

    headers = auth_headers()
    sprint_id = _create(client, headers).json()["sprint"]["id"]
    res = client.patch(
        f"/api/sprints/{sprint_id}", headers=headers, json={"status": "active", "goal": "g"}
    )
    assert res.status_code == 200
    with org_session(ORG_A) as s:
        events = list(
            s.execute(select(AuditEvent).where(AuditEvent.action == "sprint.updated")).scalars()
        )
    assert len(events) == 1
    assert events[0].detail == {"fields": ["goal", "status"]}


# --------------------------------------------------------------------------
# Sprint cards
# --------------------------------------------------------------------------


def test_add_sprint_card_204_and_upsert(client, auth_headers):
    headers = auth_headers()
    sprint_id = _create(client, headers).json()["sprint"]["id"]
    res = client.post(
        f"/api/sprints/{sprint_id}/cards",
        headers=headers,
        json={"cardId": "c1", "plannedPoints": 4.7},
    )
    assert res.status_code == 204
    assert res.content == b""
    cards = client.get(f"/api/sprints/{sprint_id}", headers=headers).json()["cards"]
    assert cards == [{"sprintId": sprint_id, "cardId": "c1", "plannedPoints": 4}]  # floored

    # Re-add updates plannedPoints in place (legacy INSERT OR REPLACE)
    res = client.post(
        f"/api/sprints/{sprint_id}/cards",
        headers=headers,
        json={"cardId": "c1", "plannedPoints": -2},
    )
    assert res.status_code == 204
    cards = client.get(f"/api/sprints/{sprint_id}", headers=headers).json()["cards"]
    assert cards == [{"sprintId": sprint_id, "cardId": "c1", "plannedPoints": 0}]  # clamped


def test_add_sprint_card_non_numeric_points_null(client, auth_headers):
    headers = auth_headers()
    sprint_id = _create(client, headers).json()["sprint"]["id"]
    res = client.post(
        f"/api/sprints/{sprint_id}/cards",
        headers=headers,
        json={"cardId": "c1", "plannedPoints": "three"},
    )
    assert res.status_code == 204
    cards = client.get(f"/api/sprints/{sprint_id}", headers=headers).json()["cards"]
    assert cards[0]["plannedPoints"] is None


def test_add_sprint_card_errors(client, auth_headers):
    headers = auth_headers()
    sprint_id = _create(client, headers).json()["sprint"]["id"]
    res = client.post("/api/sprints/abc/cards", headers=headers, json={"cardId": "c1"})
    assert res.status_code == 400
    assert res.json() == {"error": "bad id"}
    for body in ({}, {"cardId": ""}, {"cardId": 7}):
        res = client.post(f"/api/sprints/{sprint_id}/cards", headers=headers, json=body)
        assert res.status_code == 400, body
        assert res.json() == {"error": "cardId is required"}
    res = client.post("/api/sprints/999/cards", headers=headers, json={"cardId": "c1"})
    assert res.status_code == 404
    assert res.json() == {"error": "no such sprint"}


def test_remove_sprint_card_idempotent(client, auth_headers):
    headers = auth_headers()
    sprint_id = _create(client, headers).json()["sprint"]["id"]
    res = client.post(
        f"/api/sprints/{sprint_id}/cards", headers=headers, json={"cardId": "c1"}
    )
    assert res.status_code == 204
    res = client.delete(f"/api/sprints/{sprint_id}/cards/c1", headers=headers)
    assert res.status_code == 204
    assert res.content == b""
    assert client.get(f"/api/sprints/{sprint_id}", headers=headers).json()["cards"] == []
    # Absent link still 204, never 404
    res = client.delete(f"/api/sprints/{sprint_id}/cards/c1", headers=headers)
    assert res.status_code == 204
    res = client.delete("/api/sprints/abc/cards/c1", headers=headers)
    assert res.status_code == 400
    assert res.json() == {"error": "bad id"}


# --------------------------------------------------------------------------
# Cross-org isolation (RLS)
# --------------------------------------------------------------------------


def test_sprints_cross_org_isolation(client, auth_headers):
    a_headers = auth_headers(org_id=ORG_A)
    b_headers = auth_headers(org_id=ORG_B)
    sprint_id = _create(client, a_headers, name="acme only").json()["sprint"]["id"]
    res = client.post(
        f"/api/sprints/{sprint_id}/cards", headers=a_headers, json={"cardId": "c1"}
    )
    assert res.status_code == 204

    assert client.get("/api/sprints", headers=b_headers).json() == {"sprints": []}
    assert client.get(f"/api/sprints/{sprint_id}", headers=b_headers).status_code == 404
    res = client.patch(f"/api/sprints/{sprint_id}", headers=b_headers, json={"name": "x"})
    assert res.status_code == 404
    res = client.post(
        f"/api/sprints/{sprint_id}/cards", headers=b_headers, json={"cardId": "steal"}
    )
    assert res.status_code == 404
    # ORG_A's sprint and link are untouched
    body = client.get(f"/api/sprints/{sprint_id}", headers=a_headers).json()
    assert body["sprint"]["name"] == "acme only"
    assert [c["cardId"] for c in body["cards"]] == ["c1"]
