"""Audit seam integration (S3): the emit hooks fire on the paths that matter.

Auth failures, role denials, and mutations must each land a row; the query
surface is admin-only and org-scoped. Rows are asserted via the admin engine
(operator view) where the API deliberately hides them.
"""

from __future__ import annotations

from conftest import ORG_A, ORG_B
from sqlalchemy import text


def _actions(pg_urls, org_id=None) -> list[str]:
    query = "SELECT action FROM audit_events"
    params = {}
    if org_id is not None:
        query += " WHERE org_id = :org"
        params = {"org": org_id}
    with pg_urls["admin_engine"].connect() as conn:
        return [r[0] for r in conn.execute(text(query + " ORDER BY id"), params)]


def test_rejected_token_leaves_an_orgless_trace(client, pg_urls) -> None:
    client.get("/api/cards", headers={"Authorization": "Bearer garbage"})
    with pg_urls["admin_engine"].connect() as conn:
        row = conn.execute(
            text(
                "SELECT org_id, detail->>'reason' FROM audit_events"
                " WHERE action = 'auth.token_rejected' ORDER BY id DESC LIMIT 1"
            )
        ).one()
    assert row[0] is None  # no VERIFIED org to attribute -- recorded org-less
    assert row[1] == "invalid_token"


def test_missing_token_leaves_a_trace(client, pg_urls) -> None:
    client.get("/api/cards")
    assert "auth.missing_token" in _actions(pg_urls)


def test_role_denial_is_attributed(client, auth_headers, pg_urls) -> None:
    res = client.post(
        "/api/cards",
        headers=auth_headers(org_id=ORG_A, roles=["member"], sub="user_lowpriv"),
        json={"title": "nope"},
    )
    assert res.status_code == 403
    with pg_urls["admin_engine"].connect() as conn:
        row = conn.execute(
            text(
                "SELECT org_id, actor_sub FROM audit_events"
                " WHERE action = 'auth.role_denied' ORDER BY id DESC LIMIT 1"
            )
        ).one()
    assert row == (ORG_A, "user_lowpriv")


def test_mutation_audit_row_commits_with_the_mutation(client, auth_headers, pg_urls) -> None:
    res = client.post(
        "/api/cards", headers=auth_headers(roles=["admin"]), json={"title": "tracked"}
    )
    assert res.status_code == 201
    card_id = res.json()["id"]
    with pg_urls["admin_engine"].connect() as conn:
        row = conn.execute(
            text(
                "SELECT org_id, resource_type, resource_id FROM audit_events"
                " WHERE action = 'card.created'"
            )
        ).one()
    assert row == (ORG_A, "card", card_id)


def test_audit_query_is_admin_only_and_org_scoped(client, auth_headers) -> None:
    client.post("/api/cards", headers=auth_headers(roles=["admin"]), json={"title": "x"})

    member = client.get("/api/audit", headers=auth_headers(roles=["member"]))
    assert member.status_code == 403

    own = client.get("/api/audit", headers=auth_headers(roles=["admin"]))
    assert own.status_code == 200
    own_actions = [e["action"] for e in own.json()["events"]]
    assert "card.created" in own_actions
    # Pre-auth (org-less) rows never surface through the API.
    assert all(e["org_id"] == ORG_A for e in own.json()["events"])

    foreign = client.get("/api/audit", headers=auth_headers(org_id=ORG_B, roles=["admin"]))
    assert foreign.status_code == 200
    assert foreign.json()["events"] == []


def test_rolled_back_mutation_leaves_no_audit_row(client, auth_headers, pg_urls) -> None:
    # A failed request must not record a success-shaped audit event: the row
    # rides the request transaction (session=), which rolls back with it.
    res = client.post(
        "/api/cards", headers=auth_headers(roles=["admin"]), json={"title": "   "}
    )
    assert res.status_code == 400
    assert "card.created" not in _actions(pg_urls)
