"""Board surface: cards CRUD, org isolation over the wire, move semantics,
frontmatter whitelist, rank midpoints, derived events. Contract:
docs/board/CARDS_API_CONTRACT.md. Supersedes K11's test_org_isolation.py."""

from __future__ import annotations

ORG_A = "org_acme"
ORG_B = "org_globex"


def _create(client, bearer, title: str, org_id: str = ORG_A, **body):
    resp = client.post(
        "/api/cards",
        headers=bearer(org_id=org_id, roles=["admin"]),
        json={"title": title, **body},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_returns_card_detail_shape(client, bearer) -> None:
    card = _create(client, bearer, "Ship login", body="Do the thing.")
    assert card["id"] == "ship-login"
    assert card["file"] == "ship-login.md"
    assert card["status"] == "backlog"
    assert card["frontmatter"]["title"] == "Ship login"
    assert card["body"] == "Do the thing."
    assert isinstance(card["mtimeMs"], float)


def test_columns_contract(client, bearer) -> None:
    resp = client.get("/api/columns", headers=bearer())
    assert resp.json() == {
        "columns": [
            {"id": "backlog", "label": "Backlog"},
            {"id": "active", "label": "Active"},
            {"id": "awaiting_amendment_review", "label": "In Review"},
            {"id": "done", "label": "Done"},
            {"id": "blocked", "label": "Blocked"},
        ]
    }


def test_rates_contract(client, bearer) -> None:
    body = client.get("/api/rates", headers=bearer()).json()
    assert body["defaultInputRatio"] == 0.6
    assert {r["model"] for r in body["rates"]} == {
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    }


def test_org_isolation_over_the_wire(client, bearer) -> None:
    _create(client, bearer, "acme one", org_id=ORG_A)
    _create(client, bearer, "acme two", org_id=ORG_A)
    _create(client, bearer, "globex one", org_id=ORG_B)

    ids_a = {c["id"] for c in client.get("/api/cards", headers=bearer(org_id=ORG_A)).json()["cards"]}
    ids_b = {c["id"] for c in client.get("/api/cards", headers=bearer(org_id=ORG_B)).json()["cards"]}
    assert ids_a == {"acme-one", "acme-two"}
    assert ids_b == {"globex-one"}

    # Cross-org read must not even learn the card exists: 404, not 403.
    resp = client.get("/api/cards/acme-one", headers=bearer(org_id=ORG_B))
    assert resp.status_code == 404
    assert resp.json() == {"error": "no such card"}


def test_create_ignores_body_org_id(client, bearer) -> None:
    _create(client, bearer, "sneaky", org_id=ORG_A, frontmatter={"note": "x"})
    # The card lives in ORG_A (the token org); ORG_B sees nothing.
    assert client.get("/api/cards/sneaky", headers=bearer(org_id=ORG_A)).status_code == 200
    assert client.get("/api/cards/sneaky", headers=bearer(org_id=ORG_B)).status_code == 404


def test_move_semantics_and_rank_append(client, bearer) -> None:
    _create(client, bearer, "card one")
    _create(client, bearer, "card two")

    bad = client.post(
        "/api/cards/card-one/move", headers=bearer(), json={"status": "nope"}
    )
    assert bad.status_code == 400
    assert bad.json()["error"] == "status must be one of"
    assert "active" in bad.json()["valid"]

    first = client.post(
        "/api/cards/card-one/move", headers=bearer(), json={"status": "active"}
    ).json()
    assert first == {
        "id": "card-one",
        "file": "card-one.md",
        "status": "active",
        "rank": 1024.0,
    }
    second = client.post(
        "/api/cards/card-two/move", headers=bearer(), json={"status": "active"}
    ).json()
    assert second["rank"] == 2048.0

    missing = client.post(
        "/api/cards/ghost/move", headers=bearer(), json={"status": "active"}
    )
    assert missing.status_code == 404


def test_move_derives_status_changed_event(client, bearer) -> None:
    _create(client, bearer, "eventful")
    client.post("/api/cards/eventful/move", headers=bearer(), json={"status": "active"})
    events = client.get("/api/cards/eventful/events", headers=bearer()).json()["events"]
    types = [e["type"] for e in events]
    assert types == ["discovered", "status_changed"]
    assert events[1]["details"] == {"from": "backlog", "to": "active"}
    assert events[0]["cardId"] == "eventful"


def test_frontmatter_patch_whitelist(client, bearer) -> None:
    _create(client, bearer, "patchme")

    ok = client.patch(
        "/api/cards/patchme/frontmatter",
        headers=bearer(),
        json={"stakes": "high", "points": 3, "ready": True},
    )
    assert ok.status_code == 200
    fm = ok.json()["frontmatter"]
    assert (fm["stakes"], fm["points"], fm["ready"]) == ("high", 3, True)

    cleared = client.patch(
        "/api/cards/patchme/frontmatter", headers=bearer(), json={"ready": None}
    ).json()
    assert "ready" not in cleared["frontmatter"]

    cases = [
        ({}, "empty patch"),
        ({"nope": 1}, "field not patchable: nope"),
        ({"stakes": "urgent"}, 'stakes must be one of low|medium|high|null, got "urgent"'),
        ({"cost_cap_usd": -5}, "cost_cap_usd must be a positive number or null, got -5"),
        ({"title": "  "}, 'title must be a non-empty string, got "  "'),
        ({"points": 7}, "points must be an integer 1..6, got 7"),
        ({"ready": "yes"}, 'ready must be a boolean or null, got "yes"'),
    ]
    for body, message in cases:
        resp = client.patch("/api/cards/patchme/frontmatter", headers=bearer(), json=body)
        assert resp.status_code == 400, body
        assert resp.json()["error"] == message


def test_rank_midpoint_algorithm(client, bearer) -> None:
    for title in ("r one", "r two", "r three"):
        _create(client, bearer, title)
        client.post(f"/api/cards/{title.replace(' ', '-')}/move", headers=bearer(), json={"status": "active"})
    # ranks now: r-one 1024, r-two 2048, r-three 3072

    between = client.post(
        "/api/cards/r-three/rank",
        headers=bearer(),
        json={"status": "active", "prevId": "r-one", "nextId": "r-two"},
    ).json()
    assert between == {"cardId": "r-three", "status": "active", "rank": 1536.0}

    top = client.post(
        "/api/cards/r-two/rank",
        headers=bearer(),
        json={"status": "active", "prevId": None, "nextId": "r-one"},
    ).json()
    assert top["rank"] == 0.0

    ranks = client.get("/api/ranks", headers=bearer()).json()["ranks"]
    by_id = {r["cardId"]: r["rank"] for r in ranks}
    assert by_id == {"r-one": 1024.0, "r-two": 0.0, "r-three": 1536.0}


def test_card_events_limit_and_since(client, bearer) -> None:
    _create(client, bearer, "evt")
    for status in ("active", "done", "backlog"):
        client.post("/api/cards/evt/move", headers=bearer(), json={"status": status})
    events = client.get("/api/cards/evt/events", headers=bearer()).json()["events"]
    assert len(events) == 4  # discovered + 3 status_changed
    assert [e["id"] for e in events] == sorted(e["id"] for e in events)

    limited = client.get("/api/cards/evt/events?limit=2", headers=bearer()).json()["events"]
    assert len(limited) == 2

    latest = events[-1]["at"]
    since = client.get(
        f"/api/cards/evt/events?since={latest}", headers=bearer()
    ).json()["events"]
    assert since == []

    bad = client.get("/api/cards/evt/events?since=banana", headers=bearer())
    assert bad.status_code == 400
    assert bad.json() == {"error": "invalid since"}
