"""Triage lifecycle (ingest -> list -> promote/decline/merge) and the
queryable audit trail. Contract: docs/board/CARDS_API_CONTRACT.md."""

from __future__ import annotations

import sqlalchemy as sa

ORG_A = "org_acme"
ORG_B = "org_globex"

BATCH = {
    "batchId": "d260716-ab12",
    "story": "As a tester I want cards",
    "cards": [
        {
            "file": "add-login.md",
            "frontmatter": {
                "id": "add-login",
                "title": "Add login",
                "points": 2,
                "model": "claude-sonnet-4-6",
                "estimated_tokens": 50000,
                "depends_on": [],
            },
            "body": "Implement the login page." + " filler" * 80,
        },
        {
            "file": "fix-nav.md",
            "frontmatter": {"points": 1, "depends_on": ["add-login"]},
            "body": "Fix the navigation.",
        },
    ],
}


def _ingest(client, bearer, org_id=ORG_A, **overrides):
    resp = client.post(
        "/api/triage/batches",
        headers=bearer(org_id=org_id, roles=["service"]),
        json={**BATCH, **overrides},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_ingest_and_list_shapes(client, bearer) -> None:
    assert _ingest(client, bearer) == {"batchId": "d260716-ab12", "cardCount": 2}

    dupe = client.post(
        "/api/triage/batches", headers=bearer(roles=["service"]), json=BATCH
    )
    assert dupe.status_code == 409

    batches = client.get("/api/triage", headers=bearer()).json()["batches"]
    assert len(batches) == 1
    batch = batches[0]
    assert batch["batchId"] == "d260716-ab12"
    assert batch["story"] == "As a tester I want cards"
    cards = {c["id"]: c for c in batch["cards"]}
    login = cards["add-login"]
    assert login["title"] == "Add login"
    assert login["tier"] == 2
    assert login["model"] == "claude-sonnet-4-6"
    assert login["estimatedTokens"] == 50000
    assert len(login["bodyExcerpt"]) <= 280
    nav = cards["fix-nav"]
    assert nav["id"] == "fix-nav"  # falls back to file basename
    assert nav["title"] == "fix-nav"
    assert nav["dependsOn"] == ["add-login"]

    # Org isolation on triage too.
    assert client.get("/api/triage", headers=bearer(org_id=ORG_B)).json()["batches"] == []


def test_ingest_validation(client, bearer) -> None:
    service = bearer(roles=["service"])
    bad_batch = client.post(
        "/api/triage/batches",
        headers=service,
        json={"batchId": "../evil", "cards": [{"file": "a.md"}]},
    )
    assert bad_batch.status_code == 400
    bad_file = client.post(
        "/api/triage/batches",
        headers=service,
        json={"batchId": "ok1", "cards": [{"file": "../../etc/passwd.md"}]},
    )
    assert bad_file.status_code == 400
    no_cards = client.post(
        "/api/triage/batches", headers=service, json={"batchId": "ok2", "cards": []}
    )
    assert no_cards.status_code == 400


def test_promote_creates_backlog_card_and_drains_batch(client, bearer) -> None:
    _ingest(client, bearer)
    promoted = client.post(
        "/api/triage/d260716-ab12/cards/add-login.md/promote", headers=bearer()
    )
    assert promoted.status_code == 200
    assert promoted.json() == {"id": "add-login", "status": "backlog", "rank": 1024.0}

    card = client.get("/api/cards/add-login", headers=bearer()).json()
    assert card["status"] == "backlog"
    assert card["frontmatter"]["points"] == 2

    # discovered event derived on promotion
    events = client.get("/api/cards/add-login/events", headers=bearer()).json()["events"]
    assert [e["type"] for e in events] == ["discovered"]

    # Promoting again: gone from staging.
    again = client.post(
        "/api/triage/d260716-ab12/cards/add-login.md/promote", headers=bearer()
    )
    assert again.status_code == 404

    # Promote a colliding id -> 409 refusal.
    _ingest(client, bearer, batchId="d260716-cd34")
    collide = client.post(
        "/api/triage/d260716-cd34/cards/add-login.md/promote", headers=bearer()
    )
    assert collide.status_code == 409
    assert "Refusing to overwrite" in collide.json()["error"]

    # Drain the first batch fully -> it disappears from the triage list.
    assert (
        client.post(
            "/api/triage/d260716-ab12/cards/fix-nav.md/decline", headers=bearer()
        ).json()
        == {"ok": True}
    )
    remaining = client.get("/api/triage", headers=bearer()).json()["batches"]
    assert [b["batchId"] for b in remaining] == ["d260716-cd34"]


def test_merge_is_idempotent_and_retires_staged_card(client, bearer) -> None:
    client.post(
        "/api/cards",
        headers=bearer(roles=["admin"]),
        json={"title": "Target card", "body": "Original body."},
    )
    _ingest(client, bearer)

    missing_target = client.post(
        "/api/triage/d260716-ab12/cards/fix-nav.md/merge",
        headers=bearer(),
        json={"targetId": "ghost"},
    )
    assert missing_target.status_code == 404

    merged = client.post(
        "/api/triage/d260716-ab12/cards/fix-nav.md/merge",
        headers=bearer(),
        json={"targetId": "target-card"},
    )
    assert merged.json() == {"ok": True, "targetId": "target-card"}

    body = client.get("/api/cards/target-card", headers=bearer()).json()["body"]
    assert "## Absorbed from triage (fix-nav)" in body
    assert "Fix the navigation." in body
    assert body.count("Absorbed from triage") == 1

    # The staged card is retired; a retried merge finds no staged card.
    retry = client.post(
        "/api/triage/d260716-ab12/cards/fix-nav.md/merge",
        headers=bearer(),
        json={"targetId": "target-card"},
    )
    assert retry.status_code == 404


def test_audit_trail_records_mutations_and_is_queryable(client, bearer) -> None:
    admin = bearer(roles=["admin"])
    client.post("/api/cards", headers=admin, json={"title": "Audited"})
    client.post("/api/cards/audited/move", headers=bearer(), json={"status": "active"})
    client.get("/api/cards", headers=bearer())  # reads are not audited

    events = client.get("/api/audit", headers=admin).json()["events"]
    actions = [e["action"] for e in events]
    assert "card.create" in actions
    assert "card.move" in actions
    create = next(e for e in events if e["action"] == "card.create")
    assert create["actorSub"] == "user_123"
    assert create["resourceType"] == "card"
    assert create["resourceId"] == "audited"
    assert create["outcome"] == "ok"

    # Org-scoped: another org's admin sees none of it.
    other = client.get("/api/audit", headers=bearer(org_id=ORG_B, roles=["admin"]))
    assert other.json()["events"] == []


def test_auth_failures_land_in_system_audit(client, owner_engine) -> None:
    client.get("/api/cards")  # no token -> 401, audited under __system__
    client.get("/api/cards", headers={"Authorization": "Bearer garbage"})
    with owner_engine.begin() as conn:
        conn.execute(
            sa.text("SELECT set_config('app.current_org', '__system__', true)")
        )
        rows = conn.execute(
            sa.text("SELECT action, outcome FROM audit_events ORDER BY id")
        ).all()
    outcomes = [r[1] for r in rows]
    assert "missing_token" in outcomes
    assert "invalid_token" in outcomes
