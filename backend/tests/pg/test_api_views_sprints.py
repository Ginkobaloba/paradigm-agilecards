"""Saved views (org+sub scoping), sprints (rollups, patch, card links) and
retros (snake_case wire quirk). Contract: docs/board/CARDS_API_CONTRACT.md."""

from __future__ import annotations

ORG_A = "org_acme"
ORG_B = "org_globex"


# --------------------------------------------------------------------------
# Views


def test_view_crud_roundtrip(client, bearer) -> None:
    created = client.post(
        "/api/views", headers=bearer(), json={"name": "My lens", "payload": {"f": 1}}
    )
    assert created.status_code == 201
    view = created.json()
    assert view["name"] == "My lens"
    assert view["payload"] == {"f": 1}
    assert view["tokenId"] == 0  # contract deviation #3, pinned
    assert view["createdAt"].endswith("Z")

    listed = client.get("/api/views", headers=bearer()).json()["views"]
    assert [v["name"] for v in listed] == ["My lens"]

    patched = client.patch(
        f"/api/views/{view['id']}", headers=bearer(), json={"name": "Renamed"}
    ).json()
    assert patched["name"] == "Renamed"
    assert patched["payload"] == {"f": 1}

    assert client.delete(f"/api/views/{view['id']}", headers=bearer()).status_code == 204
    assert client.get("/api/views", headers=bearer()).json()["views"] == []


def test_view_validation_and_conflicts(client, bearer) -> None:
    bad_name = client.post("/api/views", headers=bearer(), json={"name": "", "payload": 1})
    assert bad_name.status_code == 400
    assert bad_name.json()["error"] == "name must be a non-empty string <= 80 chars"

    too_big = client.post(
        "/api/views", headers=bearer(), json={"name": "big", "payload": "x" * 20000}
    )
    assert too_big.status_code == 400
    assert too_big.json()["error"] == "payload too large or not JSON-serializable"

    assert client.post(
        "/api/views", headers=bearer(), json={"name": "dupe", "payload": 1}
    ).status_code == 201
    dupe = client.post("/api/views", headers=bearer(), json={"name": "dupe", "payload": 2})
    assert dupe.status_code == 409

    missing = client.patch("/api/views/99999", headers=bearer(), json={"name": "x"})
    assert missing.status_code == 404
    assert missing.json() == {"error": "no such view"}
    assert client.delete("/api/views/99999", headers=bearer()).status_code == 404


def test_views_are_scoped_per_caller(client, bearer) -> None:
    client.post(
        "/api/views", headers=bearer(sub="user_1"), json={"name": "mine", "payload": 1}
    )
    assert client.get("/api/views", headers=bearer(sub="user_2")).json()["views"] == []
    assert (
        client.get("/api/views", headers=bearer(org_id=ORG_B, sub="user_1")).json()["views"]
        == []
    )


# --------------------------------------------------------------------------
# Sprints


def _sprint(client, bearer, name="Sprint 1", **body):
    resp = client.post(
        "/api/sprints",
        headers=bearer(),
        json={"name": name, "startsAt": "2026-07-01", "endsAt": "2026-07-14", **body},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["sprint"]


def test_sprint_create_validations(client, bearer) -> None:
    no_name = client.post("/api/sprints", headers=bearer(), json={"name": "  "})
    assert no_name.status_code == 400
    assert no_name.json()["error"] == "name is required"

    no_dates = client.post("/api/sprints", headers=bearer(), json={"name": "s"})
    assert no_dates.json()["error"] == "startsAt and endsAt are required (ISO date)"

    backwards = client.post(
        "/api/sprints",
        headers=bearer(),
        json={"name": "s", "startsAt": "2026-07-14", "endsAt": "2026-07-01"},
    )
    assert backwards.json()["error"] == "endsAt cannot be before startsAt"

    bogus_status = _sprint(client, bearer, status="bogus")
    assert bogus_status["status"] == "planning"  # silently defaulted, legacy parity


def test_sprint_wire_shape_and_rollups(client, bearer) -> None:
    sprint = _sprint(client, bearer, goal="ship it")
    assert sprint["goal"] == "ship it"
    assert sprint["pointsTarget"] is None
    assert sprint["archivedAt"] is None

    sid = sprint["id"]
    assert (
        client.post(
            f"/api/sprints/{sid}/cards",
            headers=bearer(),
            json={"cardId": "c1", "plannedPoints": 3},
        ).status_code
        == 204
    )
    assert (
        client.post(
            f"/api/sprints/{sid}/cards",
            headers=bearer(),
            json={"cardId": "c2", "plannedPoints": 2.9},
        ).status_code
        == 204
    )

    summary = client.get("/api/sprints", headers=bearer()).json()["sprints"][0]
    assert summary["cardCount"] == 2
    assert summary["plannedPointsSum"] == 5  # 3 + floor(2.9)

    detail = client.get(f"/api/sprints/{sid}", headers=bearer()).json()
    links = {c["cardId"]: c["plannedPoints"] for c in detail["cards"]}
    assert links == {"c1": 3, "c2": 2}

    assert (
        client.delete(f"/api/sprints/{sid}/cards/c1", headers=bearer()).status_code == 204
    )
    # Idempotent delete, legacy parity.
    assert (
        client.delete(f"/api/sprints/{sid}/cards/c1", headers=bearer()).status_code == 204
    )


def test_sprint_patch_and_archive_filter(client, bearer) -> None:
    sid = _sprint(client, bearer)["id"]

    empty = client.patch(f"/api/sprints/{sid}", headers=bearer(), json={"bogus": 1})
    assert empty.status_code == 400
    assert empty.json()["error"] == "no recognized fields in body"

    bad_status = client.patch(
        f"/api/sprints/{sid}", headers=bearer(), json={"status": "bogus"}
    )
    assert bad_status.status_code == 400
    assert bad_status.json()["error"] == "status must be one of"

    patched = client.patch(
        f"/api/sprints/{sid}",
        headers=bearer(),
        json={"status": "active", "pointsTarget": 12.7, "dollarTarget": -4},
    ).json()["sprint"]
    assert patched["status"] == "active"
    assert patched["pointsTarget"] == 12
    assert patched["dollarTarget"] == 0

    archived = client.patch(
        f"/api/sprints/{sid}", headers=bearer(), json={"archivedAt": "2026-07-15"}
    ).json()["sprint"]
    assert archived["archivedAt"] == "2026-07-15"

    assert client.get("/api/sprints", headers=bearer()).json()["sprints"] == []
    included = client.get("/api/sprints?includeArchived=1", headers=bearer()).json()
    assert len(included["sprints"]) == 1

    assert client.get("/api/sprints/99999", headers=bearer()).status_code == 404


def test_sprints_are_org_scoped(client, bearer) -> None:
    sid = _sprint(client, bearer)["id"]
    assert (
        client.get(f"/api/sprints/{sid}", headers=bearer(org_id=ORG_B)).status_code == 404
    )


# --------------------------------------------------------------------------
# Retros (raw snake_case wire shape — preserved legacy quirk)


def test_retro_crud_and_wire_shape(client, bearer) -> None:
    missing_date = client.post("/api/retros", headers=bearer(), json={})
    assert missing_date.status_code == 400
    assert missing_date.json()["error"] == "heldOn required (ISO date)"

    sid = _sprint(client, bearer)["id"]
    created = client.post(
        "/api/retros",
        headers=bearer(),
        json={"sprintId": sid, "heldOn": "2026-07-14", "summary": "went ok"},
    )
    assert created.status_code == 201
    retro_id = created.json()["id"]

    row = client.get(f"/api/retros/{retro_id}", headers=bearer()).json()
    assert row["sprint_id"] == sid
    assert row["held_on"] == "2026-07-14"
    assert row["summary"] == "went ok"
    assert "created_at" in row

    listed = client.get("/api/retros", headers=bearer()).json()["retros"]
    assert [r["id"] for r in listed] == [retro_id]
    assert client.get("/api/retros/99999", headers=bearer()).status_code == 404
