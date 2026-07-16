"""Initial schema: tenant tables, roles, row-level security, append-only audit.

Revision ID: 0001
Revises: None

This migration IS the security contract (ADR-2026-07-16):

- ``agilecards_app`` (runtime role): NOSUPERUSER, NOBYPASSRLS, DML-only grants.
  Created idempotently as NOLOGIN; a deploy/test bootstrap grants LOGIN + a
  password (credentials never live in migrations).
- Every tenant table: ENABLE + FORCE ROW LEVEL SECURITY and an org policy
  comparing ``org_id`` to ``NULLIF(current_setting('app.current_org', true), '')``.
  Unbound context => NULL => zero rows: fail closed.
- ``audit_events``: INSERT/SELECT grants only, org-scoped SELECT policy, an
  INSERT policy that also admits org-less pre-auth events, and a trigger that
  rejects UPDATE/DELETE for everyone including the owner.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "agilecards_app"

# Tenant tables get the full org-isolation treatment. Order matters only for
# readability; policies are independent.
TENANT_TABLES = (
    "cards",
    "card_rank",
    "card_events",
    "saved_views",
    "sprints",
    "sprint_cards",
    "retros",
    "story_batches",
    "staged_cards",
)

ORG_GUC = "NULLIF(current_setting('app.current_org', true), '')"


def upgrade() -> None:
    # --- runtime role (idempotent; LOGIN/password are a bootstrap concern) ---
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )

    # --- tables ---
    op.create_table(
        "cards",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="backlog"),
        sa.Column("frontmatter", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("org_id", "id"),
        sa.CheckConstraint(
            "status IN ('backlog','active','awaiting_amendment_review','done','blocked')",
            name="cards_status_valid",
        ),
    )
    op.create_index("ix_cards_org_status", "cards", ["org_id", "status"])

    op.create_table(
        "card_rank",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rank", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "card_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "card_id"], ["cards.org_id", "cards.id"], ondelete="CASCADE"
        ),
    )

    op.create_table(
        "card_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("details", JSONB(), nullable=True),
    )
    op.create_index("ix_card_events_org_card", "card_events", ["org_id", "card_id", "id"])

    op.create_table(
        "saved_views",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("owner_sub", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("org_id", "owner_sub", "name", name="uq_saved_views_owner_name"),
    )
    op.create_index("ix_saved_views_org", "saved_views", ["org_id"])

    op.create_table(
        "sprints",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.Text(), nullable=False),
        sa.Column("ends_at", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="planning"),
        sa.Column("points_target", sa.Integer(), nullable=True),
        sa.Column("dollar_target", sa.Double(), nullable=True),
        sa.Column("review_hours_target", sa.Double(), nullable=True),
        sa.Column("archived_at", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('planning','active','completed','cancelled')",
            name="sprints_status_valid",
        ),
    )
    op.create_index("ix_sprints_org", "sprints", ["org_id"])

    op.create_table(
        "sprint_cards",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("sprint_id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Text(), nullable=False),
        sa.Column("planned_points", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("org_id", "sprint_id", "card_id"),
    )

    op.create_table(
        "retros",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("sprint_id", sa.Integer(), nullable=True),
        sa.Column("held_on", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_retros_org", "retros", ["org_id"])

    op.create_table(
        "story_batches",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("story", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="planning"),
        sa.Column("manifest", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("org_id", "batch_id"),
        sa.CheckConstraint(
            "state IN ('planning','ready','promoted','cancelled')",
            name="story_batches_state_valid",
        ),
    )

    op.create_table(
        "staged_cards",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("file", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("frontmatter", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.Text(), nullable=False, server_default="staged"),
        sa.Column("ready", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("org_id", "batch_id", "file"),
        sa.ForeignKeyConstraint(
            ["org_id", "batch_id"],
            ["story_batches.org_id", "story_batches.batch_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "state IN ('staged','promoted','declined')", name="staged_cards_state_valid"
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("org_id", sa.Text(), nullable=True),
        sa.Column("actor_sub", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("detail", JSONB(), nullable=True),
    )
    op.create_index("ix_audit_events_org_ts", "audit_events", ["org_id", "ts"])

    # --- grants: DML only; DDL stays with the owner/migration role ---
    for table in TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    # Append-only: the app role cannot UPDATE/DELETE audit rows at all.
    op.execute(f"GRANT SELECT, INSERT ON audit_events TO {APP_ROLE}")
    # Identity columns are backed by sequences the app must be able to advance.
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")

    # --- row-level security: the org guarantee lives HERE, not in Python ---
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE: even the table owner is subject to the policy.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY org_isolation ON {table}
                USING (org_id = {ORG_GUC})
                WITH CHECK (org_id = {ORG_GUC})
            """
        )

    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    # SELECT: strictly org-scoped -- NULL-org (pre-auth) rows are operator-only.
    op.execute(
        f"""
        CREATE POLICY audit_select_org ON audit_events
            FOR SELECT USING (org_id = {ORG_GUC})
        """
    )
    # INSERT: a request may write its own org's events; pre-auth paths (no org
    # context) may write org-less events. Never another org's.
    op.execute(
        f"""
        CREATE POLICY audit_insert ON audit_events
            FOR INSERT WITH CHECK (org_id IS NULL OR org_id = {ORG_GUC})
        """
    )

    # --- audit immutability trigger (belt to the missing-grant braces) ---
    op.execute(
        """
        CREATE FUNCTION audit_events_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_rewrite
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_rewrite ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_immutable()")
    for table in ("audit_events", *TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    # The role is left in place: other databases in the cluster may share it.
