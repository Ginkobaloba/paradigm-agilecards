"""Row-level security proofs (ADR-2026-07-16, audit item S2).

These tests are the difference between "org isolation" as a Python list
comprehension and org isolation as a database guarantee. Every statement here
runs through the ``agilecards_app`` role -- the production privilege level --
and several deliberately simulate the forgotten-WHERE bug: org-unfiltered
SELECT/UPDATE/DELETE must still only touch the bound org's rows, because the
*database* decides which rows exist for this session.
"""

from __future__ import annotations

import pytest
from conftest import ORG_A, ORG_B
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cards_api.models import Card


@pytest.fixture
def two_org_cards(org_session):
    with org_session(ORG_A) as s:
        s.add(Card(org_id=ORG_A, id="a1", body="alpha"))
        s.add(Card(org_id=ORG_A, id="a2", body="alpha"))
    with org_session(ORG_B) as s:
        s.add(Card(org_id=ORG_B, id="b1", body="bravo"))


def test_unfiltered_select_sees_only_bound_org(two_org_cards, org_session) -> None:
    # The forgotten-WHERE bug, read path: no org filter anywhere in the query.
    with org_session(ORG_A) as s:
        ids = {row[0] for row in s.execute(text("SELECT id FROM cards"))}
    assert ids == {"a1", "a2"}


def test_unfiltered_update_cannot_touch_foreign_rows(two_org_cards, org_session) -> None:
    # The forgotten-WHERE bug, write path: an org-unfiltered UPDATE.
    with org_session(ORG_A) as s:
        result = s.execute(text("UPDATE cards SET body = 'pwned'"))
        assert result.rowcount == 2  # only org A's rows even exist to update
    with org_session(ORG_B) as s:
        body = s.execute(text("SELECT body FROM cards WHERE id = 'b1'")).scalar_one()
    assert body == "bravo"


def test_unfiltered_delete_cannot_touch_foreign_rows(two_org_cards, org_session) -> None:
    with org_session(ORG_A) as s:
        result = s.execute(text("DELETE FROM cards"))
        assert result.rowcount == 2
    with org_session(ORG_B) as s:
        remaining = {row[0] for row in s.execute(text("SELECT id FROM cards"))}
    assert remaining == {"b1"}


def test_unbound_context_fails_closed(two_org_cards, database) -> None:
    # No org bound at all: the tenant surface must be EMPTY, not everything.
    with database.system_session() as s:
        rows = s.execute(text("SELECT id FROM cards")).all()
    assert rows == []


def test_insert_for_foreign_org_is_rejected(two_org_cards, org_session) -> None:
    # WITH CHECK: a session bound to org A cannot write rows labeled org B.
    with pytest.raises(DBAPIError, match="row-level security"), org_session(ORG_A) as s:
        s.execute(
            text("INSERT INTO cards (org_id, id) VALUES (:org, 'intruder')"),
            {"org": ORG_B},
        )


def test_update_cannot_relabel_row_into_foreign_org(two_org_cards, org_session) -> None:
    with pytest.raises(DBAPIError, match="row-level security"), org_session(ORG_A) as s:
        s.execute(text("UPDATE cards SET org_id = :org WHERE id = 'a1'"), {"org": ORG_B})


def test_every_org_table_has_forced_rls(pg_urls) -> None:
    """Future-proofing: any table that carries org_id must ship with RLS
    ENABLEd and FORCEd. A new tenant table without a policy fails this test
    before it can leak anything."""
    query = text(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped
          )
        """
    )
    with pg_urls["admin_engine"].connect() as conn:
        rows = conn.execute(query).all()
    assert rows, "expected org_id-bearing tables"
    missing = [r[0] for r in rows if not (r[1] and r[2])]
    assert not missing, f"tables with org_id but without ENABLE+FORCE RLS: {missing}"


def test_app_role_cannot_bypass_rls(pg_urls) -> None:
    query = text(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'agilecards_app'"
    )
    with pg_urls["admin_engine"].connect() as conn:
        rolsuper, rolbypassrls = conn.execute(query).one()
    assert rolsuper is False
    assert rolbypassrls is False


def test_app_role_has_no_ddl_on_tenant_tables(org_session) -> None:
    denial = pytest.raises(DBAPIError, match="(?i)must be owner|permission denied")
    with denial, org_session(ORG_A) as s:
        s.execute(text("ALTER TABLE cards DISABLE ROW LEVEL SECURITY"))


class TestAuditImmutability:
    """Compliance seam #1: the audit trail is append-only for everyone."""

    def _seed_audit_row(self, org_session) -> None:
        with org_session(ORG_A) as s:
            s.execute(
                text(
                    "INSERT INTO audit_events (org_id, actor_sub, action)"
                    " VALUES (:org, 'user_1', 'test.event')"
                ),
                {"org": ORG_A},
            )

    def test_app_role_cannot_update_audit_rows(self, org_session) -> None:
        self._seed_audit_row(org_session)
        with pytest.raises(DBAPIError, match="permission denied"), org_session(ORG_A) as s:
            s.execute(text("UPDATE audit_events SET action = 'rewritten'"))

    def test_app_role_cannot_delete_audit_rows(self, org_session) -> None:
        self._seed_audit_row(org_session)
        with pytest.raises(DBAPIError, match="permission denied"), org_session(ORG_A) as s:
            s.execute(text("DELETE FROM audit_events"))

    def test_even_owner_cannot_rewrite_audit_rows(self, org_session, pg_urls) -> None:
        # Belt and braces: the trigger binds the table owner too.
        self._seed_audit_row(org_session)
        with pg_urls["admin_engine"].connect() as conn:
            with pytest.raises(DBAPIError, match="append-only"):
                conn.execute(text("UPDATE audit_events SET action = 'rewritten'"))
            with pytest.raises(DBAPIError, match="append-only"):
                conn.execute(text("DELETE FROM audit_events"))

    def test_org_scoped_select_hides_preauth_rows(self, org_session, database) -> None:
        self._seed_audit_row(org_session)
        with database.system_session() as s:  # pre-auth writes carry no org
            s.execute(
                text("INSERT INTO audit_events (org_id, actor_sub, action)"
                     " VALUES (NULL, NULL, 'auth.token_rejected')")
            )
        with org_session(ORG_A) as s:
            actions = {r[0] for r in s.execute(text("SELECT action FROM audit_events"))}
        assert actions == {"test.event"}  # the NULL-org row is operator-only

    def test_cannot_write_foreign_org_audit_rows(self, org_session) -> None:
        with pytest.raises(DBAPIError, match="row-level security"), org_session(ORG_A) as s:
            s.execute(
                text(
                    "INSERT INTO audit_events (org_id, action)"
                    " VALUES (:org, 'forged.event')"
                ),
                {"org": ORG_B},
            )

