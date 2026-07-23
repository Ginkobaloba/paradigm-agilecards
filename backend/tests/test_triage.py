"""Triage surface: list shape, promote/decline/merge semantics, org isolation.

Wire shapes and error strings mirror legacy ``routes/triage.ts`` exactly; the
parity spec is the contract under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import ORG_A, ORG_B
from sqlalchemy import select

from cards_api.models import Card, StagedCard, StoryBatch

TRIAGE_CARD_KEYS = {
    "id",
    "title",
    "file",
    "bodyExcerpt",
    "tier",
    "model",
    "estimatedTokens",
    "dependsOn",
}


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _seed_batch(
    org_session,
    org_id: str,
    batch_id: str = "b001",
    *,
    state: str = "ready",
    story: str = "As a user I want triage.",
    cards: list[tuple[str, str, int]] | None = None,
    staged_state: str = "staged",
) -> None:
    """Seed one batch with staged cards; ``cards`` is (file, card_id, tier)."""
    if cards is None:
        cards = [
            (f"{batch_id}-01.md", f"{batch_id}-c1", 1),
            (f"{batch_id}-02.md", f"{batch_id}-c2", 2),
        ]
    with org_session(org_id) as s:
        s.add(
            StoryBatch(
                org_id=org_id, batch_id=batch_id, story=story, state=state, expires_at=_future()
            )
        )
        s.flush()  # no ORM relationship: order the batch insert before its cards
        for file, card_id, tier in cards:
            s.add(
                StagedCard(
                    org_id=org_id,
                    batch_id=batch_id,
                    file=file,
                    card_id=card_id,
                    title=f"Card {card_id}",
                    frontmatter={
                        "title": f"Card {card_id}",
                        "points": tier,
                        "model": "sonnet-4-6",
                        "estimated_tokens": 20000,
                        "depends_on": [],
                    },
                    body=f"Body of {card_id}",
                    state=staged_state,
                )
            )


def _seed_card(org_session, org_id: str, card_id: str, body: str = "Original body") -> None:
    with org_session(org_id) as s:
        s.add(
            Card(
                org_id=org_id,
                id=card_id,
                status="backlog",
                frontmatter={"title": f"Card {card_id}"},
                body=body,
            )
        )


# --------------------------------------------------------------------------
# GET /api/triage
# --------------------------------------------------------------------------


def test_list_shape_and_ordering(client, auth_headers, org_session) -> None:
    # Insert the second batch first, and its cards out of card_id order, to
    # prove the sort is server-side.
    _seed_batch(
        org_session,
        ORG_A,
        "b002",
        cards=[("b002-02.md", "b002-c2", 2), ("b002-01.md", "b002-c1", 1)],
    )
    _seed_batch(org_session, ORG_A, "b001", story="x" * 300)

    resp = client.get("/api/triage", headers=auth_headers())
    assert resp.status_code == 200
    batches = resp.json()["batches"]
    assert [b["batchId"] for b in batches] == ["b001", "b002"]
    assert batches[0]["story"] == "x" * 200  # truncated to 200 chars
    assert [c["id"] for c in batches[1]["cards"]] == ["b002-c1", "b002-c2"]

    card = batches[0]["cards"][0]
    assert set(card.keys()) == TRIAGE_CARD_KEYS
    assert card["tier"] == 1
    assert card["model"] == "sonnet-4-6"
    assert card["estimatedTokens"] == 20000
    assert card["dependsOn"] == []
    assert card["bodyExcerpt"].startswith("Body of ")


def test_list_excludes_planning_empty_and_fully_triaged(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A, "b001", state="planning")
    _seed_batch(org_session, ORG_A, "b002", cards=[])  # ready, but no staged cards
    _seed_batch(org_session, ORG_A, "b003", staged_state="declined")  # fully triaged
    _seed_batch(org_session, ORG_A, "b004")

    resp = client.get("/api/triage", headers=auth_headers())
    assert resp.status_code == 200
    assert [b["batchId"] for b in resp.json()["batches"]] == ["b004"]


# --------------------------------------------------------------------------
# POST /api/triage/{batch}/cards/{file}/promote
# --------------------------------------------------------------------------


def test_promote_happy_path(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.post("/api/triage/b001/cards/b001-01.md/promote", headers=auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "b001-c1"
    assert body["status"] == "backlog"
    assert isinstance(body["rank"], float)

    # The card is now a real backlog card with the staged content...
    card = client.get("/api/cards/b001-c1", headers=auth_headers())
    assert card.status_code == 200
    assert card.json()["status"] == "backlog"
    assert card.json()["body"] == "Body of b001-c1"

    # ...has a rank row...
    ranks = client.get("/api/ranks", headers=auth_headers()).json()["ranks"]
    assert any(r["cardId"] == "b001-c1" and r["status"] == "backlog" for r in ranks)

    # ...and is no longer offered for triage.
    triage = client.get("/api/triage", headers=auth_headers()).json()["batches"]
    listed = [c["id"] for b in triage for c in b["cards"]]
    assert "b001-c1" not in listed


def test_promote_unknown_batch_is_404(client, auth_headers) -> None:
    resp = client.post("/api/triage/nope/cards/x.md/promote", headers=auth_headers())
    assert resp.status_code == 404
    assert resp.json() == {"error": "no such staged card"}


def test_promote_unknown_file_is_404(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.post("/api/triage/b001/cards/nope.md/promote", headers=auth_headers())
    assert resp.status_code == 404
    assert resp.json() == {"error": "no such staged card"}


def test_promote_twice_is_404(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    first = client.post("/api/triage/b001/cards/b001-01.md/promote", headers=auth_headers())
    assert first.status_code == 200
    again = client.post("/api/triage/b001/cards/b001-01.md/promote", headers=auth_headers())
    assert again.status_code == 404
    assert again.json() == {"error": "no such staged card"}


def test_promote_planning_batch_is_409(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A, state="planning")
    resp = client.post("/api/triage/b001/cards/b001-01.md/promote", headers=auth_headers())
    assert resp.status_code == 409
    assert resp.json() == {"error": "batch is still planning"}


def test_promote_duplicate_backlog_card_is_409(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    _seed_card(org_session, ORG_A, "b001-c1")
    resp = client.post("/api/triage/b001/cards/b001-01.md/promote", headers=auth_headers())
    assert resp.status_code == 409
    assert resp.json() == {"error": "card already exists in backlog"}
    # The staged card stays staged: nothing was consumed by the failed promote.
    with org_session(ORG_A) as s:
        staged = s.execute(
            select(StagedCard).where(StagedCard.file == "b001-01.md")
        ).scalar_one()
        assert staged.state == "staged"


# --------------------------------------------------------------------------
# POST /api/triage/{batch}/cards/{file}/decline
# --------------------------------------------------------------------------


def test_decline(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.post("/api/triage/b001/cards/b001-01.md/decline", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    with org_session(ORG_A) as s:
        staged = s.execute(
            select(StagedCard).where(StagedCard.file == "b001-01.md")
        ).scalar_one()
        assert staged.state == "declined"
    # A declined card can no longer be promoted.
    resp = client.post("/api/triage/b001/cards/b001-01.md/promote", headers=auth_headers())
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# POST /api/triage/{batch}/cards/{file}/merge
# --------------------------------------------------------------------------


def test_merge_happy_path(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    _seed_card(org_session, ORG_A, "tgt-1")
    resp = client.post(
        "/api/triage/b001/cards/b001-01.md/merge",
        headers=auth_headers(),
        json={"targetId": "tgt-1"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "targetId": "tgt-1"}

    target = client.get("/api/cards/tgt-1", headers=auth_headers()).json()
    assert "## Absorbed from triage (b001-c1)" in target["body"]
    assert "Body of b001-c1" in target["body"]
    assert target["body"].startswith("Original body")

    with org_session(ORG_A) as s:
        staged = s.execute(
            select(StagedCard).where(StagedCard.file == "b001-01.md")
        ).scalar_one()
        assert staged.state == "declined"


def test_merge_retry_is_idempotent(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    _seed_card(org_session, ORG_A, "tgt-1")
    first = client.post(
        "/api/triage/b001/cards/b001-01.md/merge",
        headers=auth_headers(),
        json={"targetId": "tgt-1"},
    )
    assert first.status_code == 200
    # Simulate the legacy partial-failure retry: the absorbed section landed
    # but the decline did not, and the client retries the merge.
    with org_session(ORG_A) as s:
        staged = s.execute(
            select(StagedCard).where(StagedCard.file == "b001-01.md")
        ).scalar_one()
        staged.state = "staged"
    again = client.post(
        "/api/triage/b001/cards/b001-01.md/merge",
        headers=auth_headers(),
        json={"targetId": "tgt-1"},
    )
    assert again.status_code == 200
    assert again.json() == {"ok": True, "targetId": "tgt-1"}
    body = client.get("/api/cards/tgt-1", headers=auth_headers()).json()["body"]
    assert body.count("## Absorbed from triage (b001-c1)") == 1


def test_merge_requires_target_id(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    for payload in ({}, {"targetId": ""}, {"targetId": 42}):
        resp = client.post(
            "/api/triage/b001/cards/b001-01.md/merge", headers=auth_headers(), json=payload
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "targetId is required"}


def test_merge_unknown_target_is_404(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.post(
        "/api/triage/b001/cards/b001-01.md/merge",
        headers=auth_headers(),
        json={"targetId": "nope"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "no such card"}


# --------------------------------------------------------------------------
# Org isolation
# --------------------------------------------------------------------------


def test_cross_org_triage_list_is_empty(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.get("/api/triage", headers=auth_headers(org_id=ORG_B))
    assert resp.status_code == 200
    assert resp.json() == {"batches": []}


def test_cross_org_promote_is_404(client, auth_headers, org_session) -> None:
    # Org B must not even learn Org A's batch exists.
    _seed_batch(org_session, ORG_A)
    resp = client.post(
        "/api/triage/b001/cards/b001-01.md/promote", headers=auth_headers(org_id=ORG_B)
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "no such staged card"}


def test_cross_org_merge_cannot_reach_foreign_target(client, auth_headers, org_session) -> None:
    # Org B stages a card but names an Org A card as merge target: 404.
    _seed_batch(org_session, ORG_B)
    _seed_card(org_session, ORG_A, "tgt-a")
    resp = client.post(
        "/api/triage/b001/cards/b001-01.md/merge",
        headers=auth_headers(org_id=ORG_B),
        json={"targetId": "tgt-a"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "no such card"}
