"""SSE: wire format, auth via query param, after-commit delivery, and
org scoping of the bus."""

from __future__ import annotations

import asyncio
import json

from cards_api.bus import OrgEventBus


def _read_frame(lines) -> dict:
    """Read one `event:`/`data:` frame from an iter_lines iterator."""
    event_name = None
    for line in lines:
        if line.startswith("event: "):
            event_name = line[len("event: "):]
        elif line.startswith("data: "):
            payload = json.loads(line[len("data: "):])
            assert payload.get("type") == event_name
            return payload
    raise AssertionError("stream ended without a full frame")


def test_events_requires_token(client) -> None:
    assert client.get("/events").status_code == 401
    assert client.get("/events?token=garbage").status_code == 401


def test_sse_delivers_board_events_after_commit(client, bearer, make_token) -> None:
    token = make_token()
    with client.stream("GET", f"/events?token={token}") as stream:
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        lines = stream.iter_lines()

        assert _read_frame(lines) == {"type": "heartbeat"}

        created = client.post(
            "/api/cards", headers=bearer(roles=["admin"]), json={"title": "Live card"}
        )
        assert created.status_code == 201

        added = _read_frame(lines)
        assert added == {"type": "card-added", "cardId": "live-card", "status": "backlog"}
        discovered = _read_frame(lines)
        assert discovered["type"] == "card-event-added"
        assert discovered["cardId"] == "live-card"
        assert discovered["event"]["type"] == "discovered"
        assert isinstance(discovered["event"]["id"], int)


def test_bus_is_org_scoped() -> None:
    async def scenario() -> tuple[int, int]:
        bus = OrgEventBus()
        bus.attach_loop(asyncio.get_running_loop())
        queue_a = bus.subscribe("org_a")
        queue_b = bus.subscribe("org_b")
        bus.publish("org_a", {"type": "card-added", "cardId": "x"})
        await asyncio.sleep(0)  # let call_soon_threadsafe fan out
        return queue_a.qsize(), queue_b.qsize()

    sizes = asyncio.run(scenario())
    assert sizes == (1, 0)


def test_bus_unsubscribe_stops_delivery() -> None:
    async def scenario() -> int:
        bus = OrgEventBus()
        bus.attach_loop(asyncio.get_running_loop())
        queue = bus.subscribe("org_a")
        bus.unsubscribe("org_a", queue)
        bus.publish("org_a", {"type": "card-added"})
        await asyncio.sleep(0)
        return queue.qsize()

    assert asyncio.run(scenario()) == 0
