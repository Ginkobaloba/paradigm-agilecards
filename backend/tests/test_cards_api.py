"""Cards surface: wire parity with legacy routes/cards.ts + ranks.ts + events.ts.

Shapes, validation branches, and error strings are contract -- the unchanged
frontend consumes these exactly.
"""

from __future__ import annotations

import pytest
from conftest import ORG_A

from cards_api.models import Card


@pytest.fixture
def seed(org_session):
    """Three org-A cards across two columns."""
    with org_session(ORG_A) as s:
        s.add(Card(org_id=ORG_A, id="c-alpha", frontmatter={"title": "Alpha"}, body="body A"))
        s.add(Card(org_id=ORG_A, id="c-bravo", frontmatter={"title": "Bravo"}))
        s.add(Card(org_id=ORG_A, id="c-zulu", status="active", frontmatter={"title": "Zulu"}))


class TestListAndGet:
    def test_list_shape_and_ordering(self, seed, client, auth_headers) -> None:
        res = client.get("/api/cards", headers=auth_headers())
        assert res.status_code == 200
        cards = res.json()["cards"]
        # Column order first (backlog before active), then id order.
        assert [c["id"] for c in cards] == ["c-alpha", "c-bravo", "c-zulu"]
        summary = cards[0]
        assert set(summary) == {"id", "file", "status", "frontmatter", "mtimeMs"}
        assert summary["file"] == "backlog/c-alpha.md"
        assert isinstance(summary["mtimeMs"], float)
        assert cards[2]["file"] == "active/c-zulu.md"

    def test_get_detail_includes_body(self, seed, client, auth_headers) -> None:
        res = client.get("/api/cards/c-alpha", headers=auth_headers())
        assert res.status_code == 200
        assert res.json()["body"] == "body A"

    def test_get_unknown_is_404_no_such_card(self, seed, client, auth_headers) -> None:
        res = client.get("/api/cards/ghost", headers=auth_headers())
        assert res.status_code == 404
        assert res.json() == {"error": "no such card"}


class TestFrontmatterPatch:
    def test_404_before_validation(self, seed, client, auth_headers) -> None:
        res = client.patch(
            "/api/cards/ghost/frontmatter", headers=auth_headers(), json={"bogus": 1}
        )
        assert res.status_code == 404
        assert res.json()["error"] == "no such card"

    def test_empty_patch_rejected(self, seed, client, auth_headers) -> None:
        res = client.patch("/api/cards/c-alpha/frontmatter", headers=auth_headers(), json={})
        assert res.status_code == 400
        assert res.json()["error"] == "empty patch"

    def test_non_whitelisted_field_rejected(self, seed, client, auth_headers) -> None:
        res = client.patch(
            "/api/cards/c-alpha/frontmatter", headers=auth_headers(), json={"id": "hax"}
        )
        assert res.status_code == 400
        assert res.json()["error"] == "field not patchable: id"

    @pytest.mark.parametrize(
        ("patch", "message_part"),
        [
            ({"stakes": "extreme"}, "stakes"),
            ({"cost_cap_usd": -3}, "cost_cap_usd"),
            ({"cost_cap_usd": "12"}, "cost_cap_usd"),
            ({"title": "   "}, "title"),
            ({"points": 0}, "points"),
            ({"points": 7}, "points"),
            ({"points": 2.5}, "points"),
            ({"ready": "yes"}, "ready"),
        ],
    )
    def test_validation_branches(self, seed, client, auth_headers, patch, message_part) -> None:
        res = client.patch(
            "/api/cards/c-alpha/frontmatter", headers=auth_headers(), json=patch
        )
        assert res.status_code == 400
        assert message_part in res.json()["error"]

    def test_patch_updates_and_null_deletes(self, seed, client, auth_headers) -> None:
        res = client.patch(
            "/api/cards/c-alpha/frontmatter",
            headers=auth_headers(),
            json={"stakes": "high", "points": 3, "title": "Alpha v2", "ready": True},
        )
        assert res.status_code == 200
        fm = res.json()["frontmatter"]
        assert fm["stakes"] == "high" and fm["points"] == 3 and fm["ready"] is True
        assert fm["title"] == "Alpha v2"

        res = client.patch(
            "/api/cards/c-alpha/frontmatter",
            headers=auth_headers(),
            json={"stakes": None, "ready": None},
        )
        fm = res.json()["frontmatter"]
        assert "stakes" not in fm and "ready" not in fm
        assert fm["points"] == 3  # untouched fields survive


class TestMove:
    def test_invalid_status_lists_valid_ids(self, seed, client, auth_headers) -> None:
        res = client.post(
            "/api/cards/c-alpha/move", headers=auth_headers(), json={"status": "limbo"}
        )
        assert res.status_code == 400
        body = res.json()
        assert body["error"] == "status must be one of"
        assert "backlog" in body["valid"] and len(body["valid"]) == 5

    def test_unknown_card_is_409(self, seed, client, auth_headers) -> None:
        res = client.post(
            "/api/cards/ghost/move", headers=auth_headers(), json={"status": "active"}
        )
        assert res.status_code == 409

    def test_cross_column_move(self, seed, client, auth_headers) -> None:
        res = client.post(
            "/api/cards/c-alpha/move", headers=auth_headers(), json={"status": "active"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "active"
        assert body["file"] == "active/c-alpha.md"  # folder tracks status
        assert body["rank"] > 0

        events = client.get("/api/cards/c-alpha/events", headers=auth_headers()).json()[
            "events"
        ]
        assert [e["type"] for e in events] == ["status_changed"]
        assert events[0]["details"] == {"from": "backlog", "to": "active"}

    def test_same_status_move_still_appends_rank(self, seed, client, auth_headers) -> None:
        first = client.post(
            "/api/cards/c-alpha/move", headers=auth_headers(), json={"status": "backlog"}
        ).json()
        second = client.post(
            "/api/cards/c-bravo/move", headers=auth_headers(), json={"status": "backlog"}
        ).json()
        assert second["rank"] == first["rank"] + 1024
        # No status_changed event for a no-op move.
        events = client.get("/api/cards/c-alpha/events", headers=auth_headers()).json()
        assert events["events"] == []


class TestRanks:
    def _move(self, client, headers, card_id, status="backlog") -> float:
        return client.post(
            f"/api/cards/{card_id}/move", headers=headers, json={"status": status}
        ).json()["rank"]

    def test_rank_midpoint_algorithm(self, seed, client, auth_headers) -> None:
        headers = auth_headers()
        rank_alpha = self._move(client, headers, "c-alpha")  # 1024
        rank_bravo = self._move(client, headers, "c-bravo")  # 2048
        assert (rank_alpha, rank_bravo) == (1024.0, 2048.0)

        # Between both neighbors -> midpoint.
        res = client.post(
            "/api/cards/c-zulu/rank",
            headers=headers,
            json={"status": "backlog", "prevId": "c-alpha", "nextId": "c-bravo"},
        )
        assert res.status_code == 200
        assert res.json() == {"cardId": "c-zulu", "status": "backlog", "rank": 1536.0}

        # prev only -> prev + step.
        res = client.post(
            "/api/cards/c-zulu/rank",
            headers=headers,
            json={"status": "backlog", "prevId": "c-bravo", "nextId": None},
        )
        assert res.json()["rank"] == 3072.0

        # next only -> next - step.
        res = client.post(
            "/api/cards/c-zulu/rank",
            headers=headers,
            json={"status": "backlog", "prevId": None, "nextId": "c-alpha"},
        )
        assert res.json()["rank"] == 0.0

    def test_rank_in_empty_column_is_base(self, seed, client, auth_headers) -> None:
        res = client.post(
            "/api/cards/c-alpha/rank",
            headers=auth_headers(),
            json={"status": "done", "prevId": None, "nextId": None},
        )
        assert res.json()["rank"] == 1024.0

    def test_rank_bad_status_rejected(self, seed, client, auth_headers) -> None:
        res = client.post(
            "/api/cards/c-alpha/rank", headers=auth_headers(), json={"status": "limbo"}
        )
        assert res.status_code == 400
        assert res.json()["error"] == "status must be one of"

    def test_rank_unknown_card_is_404(self, seed, client, auth_headers) -> None:
        # Documented divergence from legacy (which accepted ranks for cards
        # "not yet on disk"): the FK-backed store 404s instead.
        res = client.post(
            "/api/cards/ghost/rank", headers=auth_headers(), json={"status": "backlog"}
        )
        assert res.status_code == 404

    def test_list_ranks_shape(self, seed, client, auth_headers) -> None:
        headers = auth_headers()
        self._move(client, headers, "c-alpha")
        res = client.get("/api/ranks", headers=headers)
        assert res.status_code == 200
        ranks = res.json()["ranks"]
        assert {"cardId": "c-alpha", "status": "backlog", "rank": 1024.0} in ranks


class TestCardEvents:
    def test_limit_and_since(self, seed, client, auth_headers) -> None:
        headers = auth_headers()
        for status in ("active", "done", "blocked"):
            client.post("/api/cards/c-alpha/move", headers=headers, json={"status": status})
        all_events = client.get("/api/cards/c-alpha/events", headers=headers).json()["events"]
        assert [e["details"]["to"] for e in all_events] == ["active", "done", "blocked"]

        limited = client.get(
            "/api/cards/c-alpha/events", headers=headers, params={"limit": "2"}
        ).json()["events"]
        assert len(limited) == 2

        since = all_events[0]["at"]
        later = client.get(
            "/api/cards/c-alpha/events", headers=headers, params={"since": since}
        ).json()["events"]
        assert [e["id"] for e in later] == [e["id"] for e in all_events[1:]]


class TestSse:
    # NOTE: no full-stream heartbeat test through TestClient here -- an
    # infinite SSE generator cannot be cleanly cancelled through the sync
    # test portal (it hangs on close). The reject paths below still exercise
    # the real HTTP surface (auth fails before streaming starts); the accept
    # path is proven at the dependency + bus layers, and end-to-end by the
    # deploy stack's curl smoke (deploy/agilecards/README.md).

    def test_events_requires_token(self, client) -> None:
        res = client.get("/events")
        assert res.status_code == 401

    def test_bad_query_token_is_401(self, client) -> None:
        res = client.get("/events?token=garbage")
        assert res.status_code == 401
        assert res.json()["error"] == "invalid_token"

    def test_query_token_accepted_by_sse_auth_dependency(
        self, client, verifier, make_token
    ) -> None:
        # EventSource cannot set headers; ?token= is the documented exception,
        # accepted by require_claims_header_or_query and nowhere else.
        from urllib.parse import urlencode

        from starlette.requests import Request

        from cards_api.deps import require_claims_header_or_query

        token = make_token(org_id=ORG_A, sub="sse_user")
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/events",
            "headers": [],
            "query_string": urlencode({"token": token}).encode(),
            "app": client.app,
        }
        claims = require_claims_header_or_query(Request(scope), None, verifier)
        assert claims.org_id == ORG_A and claims.sub == "sse_user"

    def test_header_token_never_reaches_regular_routes_via_query(
        self, client, make_token
    ) -> None:
        # ?token= must NOT authenticate ordinary API routes.
        res = client.get(f"/api/cards?token={make_token()}")
        assert res.status_code == 401

    def test_event_bus_is_org_scoped(self) -> None:
        import asyncio

        from cards_api.events import EventBus

        async def run() -> tuple[list, bool]:
            bus = EventBus()
            token_a, queue_a = bus.subscribe(ORG_A)
            token_b, queue_b = bus.subscribe("org_other")
            bus.publish(ORG_A, {"type": "card-added", "cardId": "x"})
            await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run
            got_a = [queue_a.get_nowait()]
            bus.unsubscribe(token_a)
            bus.unsubscribe(token_b)
            return got_a, queue_b.empty()

        got_a, b_empty = asyncio.run(run())
        assert got_a == [{"type": "card-added", "cardId": "x"}]
        assert b_empty is True

    def test_mutations_publish_bus_events(self, seed, client, auth_headers) -> None:
        published: list[tuple[str, dict]] = []
        client.app.state.bus.publish = lambda org, payload: published.append((org, payload))

        client.post(
            "/api/cards/c-alpha/move", headers=auth_headers(), json={"status": "active"}
        )
        types = [p["type"] for _, p in published]
        assert "card-state-changed" in types and "card-event-added" in types
        assert all(org == ORG_A for org, _ in published)

    def test_failed_mutation_publishes_nothing(self, seed, client, auth_headers) -> None:
        published: list[dict] = []
        client.app.state.bus.publish = lambda org, payload: published.append(payload)
        client.post(
            "/api/cards/c-alpha/move", headers=auth_headers(), json={"status": "limbo"}
        )
        assert published == []
