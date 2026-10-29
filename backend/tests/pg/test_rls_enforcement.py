"""Proofs that org isolation is enforced BY THE DATABASE, not by app code.

Every test here talks raw SQL through the ``cards_app`` role — the exact
credentials the application uses — and deliberately simulates buggy or
malicious application code (forgotten WHERE clauses, foreign org_id writes,
attempts to switch RLS off). If any of these pass because of a Python-side
filter, they are worthless; that is why they bypass the app entirely.

Audit trace: S2 ("org isolation is app-layer only") and the D3 decision in
docs/adr/ADR-2026-07-16-cards-api-postgres-rls.md.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from .conftest import TENANT_TABLES, set_org

ORG_A = "org_acme"
ORG_B = "org_globex"


def _insert_card(conn, org_id: str, card_id: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO cards (org_id, id, file, status, frontmatter, body) "
            "VALUES (:org, :id, :file, 'backlog', '{}'::jsonb, '')"
        ),
        {"org": org_id, "id": card_id, "file": f"{card_id}.md"},
    )


def test_insert_and_read_within_org(app_engine):
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        _insert_card(conn, ORG_A, "a1")
        rows = conn.execute(sa.text("SELECT id FROM cards")).scalars().all()
    assert rows == ["a1"]


def test_no_org_context_reads_nothing(app_engine):
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        _insert_card(conn, ORG_A, "a1")
    # New transaction, NO org context set: fail closed, zero rows.
    with app_engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT * FROM cards")).all()
    assert rows == []


def test_no_org_context_cannot_write(app_engine):
    with app_engine.begin() as conn:
        with pytest.raises(sa.exc.DBAPIError):
            _insert_card(conn, ORG_A, "a1")


def test_forgotten_where_clause_leaks_nothing_cross_org(app_engine):
    """The S2 scenario verbatim: app code SELECTs with no org filter."""
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        _insert_card(conn, ORG_A, "a1")
    with app_engine.begin() as conn:
        set_org(conn, ORG_B)
        _insert_card(conn, ORG_B, "b1")
        # Buggy query: no WHERE org_id — RLS must scope it to org B anyway.
        rows = conn.execute(sa.text("SELECT id FROM cards")).scalars().all()
    assert rows == ["b1"]


def test_write_with_foreign_org_id_rejected(app_engine):
    """WITH CHECK: a session in org A cannot manufacture org-B rows."""
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        with pytest.raises(sa.exc.DBAPIError):
            _insert_card(conn, ORG_B, "smuggled")


def test_cross_org_update_and_delete_hit_zero_rows(app_engine):
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        _insert_card(conn, ORG_A, "a1")
    with app_engine.begin() as conn:
        set_org(conn, ORG_B)
        updated = conn.execute(
            sa.text("UPDATE cards SET body = 'pwned'")
        ).rowcount
        deleted = conn.execute(sa.text("DELETE FROM cards")).rowcount
    assert (updated, deleted) == (0, 0)
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        body = conn.execute(sa.text("SELECT body FROM cards WHERE id='a1'")).scalar()
    assert body == ""


def test_update_cannot_reassign_org(app_engine):
    """UPDATE ... SET org_id = <other org> must violate WITH CHECK."""
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        _insert_card(conn, ORG_A, "a1")
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        with pytest.raises(sa.exc.DBAPIError):
            conn.execute(sa.text("UPDATE cards SET org_id = :b"), {"b": ORG_B})


def test_app_role_cannot_disable_rls(app_engine):
    """ALTER TABLE is an owner privilege; the app role must not have it."""
    with app_engine.connect() as conn:
        with pytest.raises(sa.exc.DBAPIError):
            conn.execute(sa.text("ALTER TABLE cards DISABLE ROW LEVEL SECURITY"))


def test_app_role_has_no_bypassrls(app_engine):
    with app_engine.connect() as conn:
        bypass = conn.execute(
            sa.text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar()
    assert bypass is False


def test_owner_is_forced_through_rls(owner_engine, app_engine):
    """FORCE ROW LEVEL SECURITY: even the table owner gets zero rows without
    an org context (belt + suspenders against misconfigured deploy URLs)."""
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        _insert_card(conn, ORG_A, "a1")
    with owner_engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT * FROM cards")).all()
    assert rows == []


def test_every_tenant_table_has_rls_enabled_and_forced(owner_engine):
    """Structural check: a future table added without RLS should fail CI."""
    with owner_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "AND relname != 'alembic_version'"
            )
        ).all()
    found = {r[0]: (r[1], r[2]) for r in rows}
    assert set(found) == set(TENANT_TABLES)
    not_locked = {t: v for t, v in found.items() if v != (True, True)}
    assert not_locked == {}, f"tables missing ENABLE/FORCE RLS: {not_locked}"


def test_audit_log_is_append_only_for_app_role(app_engine):
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        conn.execute(
            sa.text(
                "INSERT INTO audit_events (org_id, actor_sub, action, outcome) "
                "VALUES (:org, 'user_1', 'card.create', 'ok')"
            ),
            {"org": ORG_A},
        )
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        with pytest.raises(sa.exc.DBAPIError):
            conn.execute(sa.text("UPDATE audit_events SET outcome = 'scrubbed'"))
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        with pytest.raises(sa.exc.DBAPIError):
            conn.execute(sa.text("DELETE FROM audit_events"))


def test_card_events_are_append_only_for_app_role(app_engine):
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        conn.execute(
            sa.text(
                "INSERT INTO card_events (org_id, card_id, type, at) "
                "VALUES (:org, 'a1', 'discovered', now())"
            ),
            {"org": ORG_A},
        )
    with app_engine.begin() as conn:
        set_org(conn, ORG_A)
        with pytest.raises(sa.exc.DBAPIError):
            conn.execute(sa.text("DELETE FROM card_events"))
