"""Stories surface: submit (SSE-over-POST), approve, cancel, pending.

Submit streams against the demo planner (``PARADIGM_STORIES_PLANNER=demo``),
which is deterministic, so the dry-run payload is asserted exactly. Approve /
cancel / pending are plain JSON routes seeded directly through org-bound
sessions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from conftest import ORG_A, ORG_B
from sqlalchemy import select

from cards_api.models import Card, StagedCard, StoryBatch


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Split a buffered SSE body into (event, payload) frames, in order."""
    frames: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event is not None:
            frames.append((event, data))
    return frames


def _seed_batch(
    org_session,
    org_id: str,
    batch_id: str = "s001",
    *,
    state: str = "ready",
    story: str = "As a user I want stories.",
    expires_at: datetime | None = None,
    card_ids: tuple[str, ...] = ("s001-c1", "s001-c2"),
) -> None:
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(hours=1)
    with org_session(org_id) as s:
        s.add(
            StoryBatch(
                org_id=org_id, batch_id=batch_id, story=story, state=state, expires_at=expires_at
            )
        )
        s.flush()  # no ORM relationship: order the batch insert before its cards
        for card_id in card_ids:
            s.add(
                StagedCard(
                    org_id=org_id,
                    batch_id=batch_id,
                    file=f"{card_id}.md",
                    card_id=card_id,
                    title=f"Card {card_id}",
                    frontmatter={"title": f"Card {card_id}", "points": 2, "depends_on": []},
                    body=f"Body of {card_id}",
                    state="staged",
                )
            )


# --------------------------------------------------------------------------
# POST /api/stories/submit
# --------------------------------------------------------------------------


def test_submit_streams_progress_then_dry_run(client, auth_headers, monkeypatch) -> None:
    monkeypatch.setenv("PARADIGM_STORIES_PLANNER", "demo")
    resp = client.post(
        "/api/stories/submit", headers=auth_headers(), json={"story": "Build the widget."}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse(resp.text)
    assert len(frames) >= 3
    # Every frame before the final dry_run is a progress frame carrying batch_id.
    assert frames[-1][0] == "dry_run"  # and nothing after it: no "complete" event
    for event, payload in frames[:-1]:
        assert event == "progress"
        assert payload["batch_id"]
        assert payload["step"] == "plan"

    dry_run = frames[-1][1]
    assert dry_run["batch_id"] == frames[0][1]["batch_id"]
    assert [c["id"] for c in dry_run["cards"]] == ["demo-01", "demo-02", "demo-03"]
    assert dry_run["histogram"] == {"1": 1, "2": 1, "3": 1}
    assert dry_run["depends_on_edges"] == [["demo-02", "demo-01"]]
    assert dry_run["claimable_count"] == 2
    assert dry_run["mode"] == "full"
    assert dry_run["deep_planning"] is False
    for card in dry_run["cards"]:
        assert "Build the widget." in card["bodyExcerpt"]


def test_submit_creates_a_ready_pending_batch(client, auth_headers, monkeypatch) -> None:
    monkeypatch.setenv("PARADIGM_STORIES_PLANNER", "demo")
    resp = client.post(
        "/api/stories/submit", headers=auth_headers(), json={"story": "Plan me."}
    )
    batch_id = _parse_sse(resp.text)[-1][1]["batch_id"]

    pending = client.get("/api/stories/pending", headers=auth_headers()).json()["pending"]
    assert [p["batchId"] for p in pending] == [batch_id]
    assert pending[0]["cardCount"] == 3
    assert pending[0]["story"] == "Plan me."
    assert pending[0]["expiresAt"]

    # The batch is also offered for card-by-card triage.
    triage = client.get("/api/triage", headers=auth_headers()).json()["batches"]
    assert [b["batchId"] for b in triage] == [batch_id]
    assert len(triage[0]["cards"]) == 3


def test_submit_mode_and_deep_planning_flags(client, auth_headers, monkeypatch) -> None:
    monkeypatch.setenv("PARADIGM_STORIES_PLANNER", "demo")
    resp = client.post(
        "/api/stories/submit",
        headers=auth_headers(),
        json={"story": "Lean run.", "mode": "lean", "deep_planning": True},
    )
    dry_run = _parse_sse(resp.text)[-1][1]
    assert dry_run["mode"] == "lean"
    assert dry_run["deep_planning"] is True

    # deep_planning must be JSON true exactly; truthy strings count as false.
    resp = client.post(
        "/api/stories/submit",
        headers=auth_headers(),
        json={"story": "Lean run.", "deep_planning": "yes"},
    )
    assert _parse_sse(resp.text)[-1][1]["deep_planning"] is False


def test_submit_validation_errors(client, auth_headers) -> None:
    # Validation precedes planner resolution: plain 400s, never a stream.
    for payload in ({}, {"story": ""}, {"story": 42}, [1, 2]):
        resp = client.post("/api/stories/submit", headers=auth_headers(), json=payload)
        assert resp.status_code == 400
        assert resp.json() == {"error": "story is required"}

    resp = client.post(
        "/api/stories/submit", headers=auth_headers(), json={"story": "x" * 65537}
    )
    assert resp.status_code == 400
    assert resp.json() == {"error": "story too large (max 65536 bytes)"}

    resp = client.post(
        "/api/stories/submit", headers=auth_headers(), json={"story": "ok", "mode": "turbo"}
    )
    assert resp.status_code == 400
    assert resp.json() == {"error": "mode must be full or lean"}


def test_submit_without_planner_is_501(client, auth_headers, monkeypatch) -> None:
    monkeypatch.delenv("PARADIGM_STORIES_PLANNER", raising=False)
    resp = client.post("/api/stories/submit", headers=auth_headers(), json={"story": "ok"})
    assert resp.status_code == 501
    assert resp.json() == {"error": "story planning is not available on this deployment"}


# --------------------------------------------------------------------------
# POST /api/stories/{batch_id}/approve
# --------------------------------------------------------------------------


def test_approve_happy_path(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.post("/api/stories/s001/approve", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"batchId": "s001", "cardsWritten": 2}

    listed = {c["id"] for c in client.get("/api/cards", headers=auth_headers()).json()["cards"]}
    assert {"s001-c1", "s001-c2"} <= listed
    card = client.get("/api/cards/s001-c1", headers=auth_headers()).json()
    assert card["status"] == "backlog"
    assert card["body"] == "Body of s001-c1"

    with org_session(ORG_A) as s:
        batch = s.execute(select(StoryBatch).where(StoryBatch.batch_id == "s001")).scalar_one()
        assert batch.state == "promoted"
        states = s.execute(select(StagedCard.state).where(StagedCard.batch_id == "s001")).all()
        assert all(state == "promoted" for (state,) in states)

    # A promoted batch is no longer pending: a second approve is 404.
    again = client.post("/api/stories/s001/approve", headers=auth_headers())
    assert again.status_code == 404


def test_approve_unknown_batch_is_404(client, auth_headers) -> None:
    resp = client.post("/api/stories/nope/approve", headers=auth_headers())
    assert resp.status_code == 404
    assert resp.json() == {"error": "no pending batch nope"}


def test_approve_expired_batch_is_404(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    with org_session(ORG_A) as s:
        batch = s.execute(select(StoryBatch).where(StoryBatch.batch_id == "s001")).scalar_one()
        batch.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    resp = client.post("/api/stories/s001/approve", headers=auth_headers())
    assert resp.status_code == 404
    assert resp.json() == {"error": "no pending batch s001"}


def test_approve_planning_batch_is_404(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A, state="planning")
    resp = client.post("/api/stories/s001/approve", headers=auth_headers())
    assert resp.status_code == 404


def test_approve_duplicate_card_rolls_back_everything(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)  # s001-c1, s001-c2
    with org_session(ORG_A) as s:
        s.add(Card(org_id=ORG_A, id="s001-c2", status="backlog", frontmatter={}, body=""))
    resp = client.post("/api/stories/s001/approve", headers=auth_headers())
    assert resp.status_code == 409
    assert resp.json() == {"error": "card already exists in backlog"}

    # All-or-nothing: the non-conflicting card was rolled back too, and the
    # batch is still pending.
    listed = {c["id"] for c in client.get("/api/cards", headers=auth_headers()).json()["cards"]}
    assert "s001-c1" not in listed
    with org_session(ORG_A) as s:
        batch = s.execute(select(StoryBatch).where(StoryBatch.batch_id == "s001")).scalar_one()
        assert batch.state == "ready"


# --------------------------------------------------------------------------
# POST /api/stories/{batch_id}/cancel
# --------------------------------------------------------------------------


def test_cancel(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.post("/api/stories/s001/cancel", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    with org_session(ORG_A) as s:
        batch = s.execute(select(StoryBatch).where(StoryBatch.batch_id == "s001")).scalar_one()
        assert batch.state == "cancelled"
        states = s.execute(select(StagedCard.state).where(StagedCard.batch_id == "s001")).all()
        assert all(state == "declined" for (state,) in states)
    pending = client.get("/api/stories/pending", headers=auth_headers()).json()["pending"]
    assert pending == []


def test_cancel_is_best_effort(client, auth_headers, org_session) -> None:
    # Unknown batches and already-promoted batches still answer ok (legacy:
    # the frontend ignores cancel failures).
    resp = client.post("/api/stories/nope/cancel", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    _seed_batch(org_session, ORG_A)
    client.post("/api/stories/s001/approve", headers=auth_headers())
    resp = client.post("/api/stories/s001/cancel", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    with org_session(ORG_A) as s:
        batch = s.execute(select(StoryBatch).where(StoryBatch.batch_id == "s001")).scalar_one()
        assert batch.state == "promoted"  # cancel never un-promotes


# --------------------------------------------------------------------------
# GET /api/stories/pending
# --------------------------------------------------------------------------


def test_pending_lists_only_live_ready_batches(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A, "s001", story="y" * 300)
    _seed_batch(
        org_session,
        ORG_A,
        "s002",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        card_ids=("s002-c1",),
    )
    _seed_batch(org_session, ORG_A, "s003", state="planning", card_ids=("s003-c1",))

    resp = client.get("/api/stories/pending", headers=auth_headers())
    assert resp.status_code == 200
    pending = resp.json()["pending"]
    assert [p["batchId"] for p in pending] == ["s001"]
    entry = pending[0]
    assert set(entry.keys()) == {"batchId", "story", "cardCount", "expiresAt"}
    assert entry["story"] == "y" * 200  # truncated to 200 chars
    assert entry["cardCount"] == 2
    assert datetime.fromisoformat(entry["expiresAt"]) > datetime.now(UTC)


# --------------------------------------------------------------------------
# Org isolation
# --------------------------------------------------------------------------


def test_cross_org_approve_is_404(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.post("/api/stories/s001/approve", headers=auth_headers(org_id=ORG_B))
    assert resp.status_code == 404
    assert resp.json() == {"error": "no pending batch s001"}
    # And Org A's batch is untouched.
    with org_session(ORG_A) as s:
        batch = s.execute(select(StoryBatch).where(StoryBatch.batch_id == "s001")).scalar_one()
        assert batch.state == "ready"


def test_cross_org_pending_is_empty(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.get("/api/stories/pending", headers=auth_headers(org_id=ORG_B))
    assert resp.status_code == 200
    assert resp.json() == {"pending": []}


def test_cross_org_cancel_does_not_touch_foreign_batch(client, auth_headers, org_session) -> None:
    _seed_batch(org_session, ORG_A)
    resp = client.post("/api/stories/s001/cancel", headers=auth_headers(org_id=ORG_B))
    assert resp.status_code == 200  # best-effort ok, but nothing changed
    with org_session(ORG_A) as s:
        batch = s.execute(select(StoryBatch).where(StoryBatch.batch_id == "s001")).scalar_one()
        assert batch.state == "ready"
