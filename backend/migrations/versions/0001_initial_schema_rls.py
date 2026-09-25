"""Initial Cards API schema: tenant tables, RLS policies, roles, grants.

Raw SQL on purpose — the RLS policies and grants ARE the security boundary
(audit S2 / ADR D3) and must be reviewable as literal SQL. Every tenant table:

- carries ``org_id TEXT NOT NULL``,
- has ROW LEVEL SECURITY both ENABLED and FORCED (owner obeys too),
- gets a single policy binding all commands to
  ``current_setting('app.current_org', true)`` — NULL when unset, so an
  org-less session fails closed.

The runtime role ``cards_app`` is created NOLOGIN here (idempotently; roles
are cluster-level). Deploy/test provisioning attaches LOGIN + a password.
``cards_app`` never owns tables and never gets UPDATE/DELETE on the two
append-only logs (``card_events``, ``audit_events``).

Revision ID: 0001
Revises:
Create Date: 2026-07-16
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATUSES = "('backlog','active','awaiting_amendment_review','done','blocked')"

# (table, app-role privileges). SELECT/INSERT only == append-only log.
TABLES: list[tuple[str, str]] = []

_CARDS = f"""
CREATE TABLE cards (
    org_id      text NOT NULL,
    id          text NOT NULL,
    file        text NOT NULL,
    status      text NOT NULL CHECK (status IN {STATUSES}),
    frontmatter jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    body        text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, id),
    UNIQUE (org_id, file)
)
"""

_CARD_RANKS = """
CREATE TABLE card_ranks (
    org_id     text NOT NULL,
    card_id    text NOT NULL,
    status     text NOT NULL,
    rank       double precision NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, card_id)
)
"""

_CARD_EVENTS = """
CREATE TABLE card_events (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id     text NOT NULL,
    card_id    text NOT NULL,
    type       text NOT NULL,
    at         timestamptz NOT NULL,
    details    jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
)
"""

_SAVED_VIEWS = """
CREATE TABLE saved_views (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id     text NOT NULL,
    owner_sub  text NOT NULL,
    name       text NOT NULL,
    payload    jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, owner_sub, name)
)
"""

# starts_at/ends_at/archived_at are ISO strings passed through verbatim —
# wire fidelity with the legacy contract (string ordering == date ordering
# for ISO-8601). See CARDS_API_CONTRACT.md.
_SPRINTS = """
CREATE TABLE sprints (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id              text NOT NULL,
    name                text NOT NULL,
    starts_at           text NOT NULL,
    ends_at             text NOT NULL,
    goal                text,
    status              text NOT NULL DEFAULT 'planning'
                        CHECK (status IN ('planning','active','completed','cancelled')),
    points_target       integer,
    dollar_target       double precision,
    review_hours_target double precision,
    archived_at         text,
    created_at          timestamptz NOT NULL DEFAULT now()
)
"""

_SPRINT_CARDS = """
CREATE TABLE sprint_cards (
    org_id         text NOT NULL,
    sprint_id      bigint NOT NULL REFERENCES sprints(id) ON DELETE CASCADE,
    card_id        text NOT NULL,
    planned_points integer,
    PRIMARY KEY (org_id, sprint_id, card_id)
)
"""

_RETROS = """
CREATE TABLE retros (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id     text NOT NULL,
    sprint_id  bigint REFERENCES sprints(id) ON DELETE SET NULL,
    held_on    text NOT NULL,
    summary    text,
    created_at timestamptz NOT NULL DEFAULT now()
)
"""

_TRIAGE_BATCHES = """
CREATE TABLE triage_batches (
    org_id     text NOT NULL,
    batch_id   text NOT NULL,
    story      text,
    state      text NOT NULL DEFAULT 'ready'
               CHECK (state IN ('planning','ready','finalized')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, batch_id)
)
"""

_TRIAGE_CARDS = """
CREATE TABLE triage_cards (
    org_id      text NOT NULL,
    batch_id    text NOT NULL,
    file        text NOT NULL,
    card_id     text NOT NULL,
    title       text NOT NULL,
    frontmatter jsonb NOT NULL DEFAULT '{}'::jsonb,
    body        text NOT NULL DEFAULT '',
    state       text NOT NULL DEFAULT 'staged'
                CHECK (state IN ('staged','promoted','declined','merged')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, batch_id, file),
    FOREIGN KEY (org_id, batch_id)
        REFERENCES triage_batches(org_id, batch_id) ON DELETE CASCADE
)
"""

_AUDIT_EVENTS = """
CREATE TABLE audit_events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id        text NOT NULL,
    at            timestamptz NOT NULL DEFAULT now(),
    actor_sub     text,
    action        text NOT NULL,
    resource_type text,
    resource_id   text,
    outcome       text NOT NULL,
    details       jsonb
)
"""

_FULL = "SELECT, INSERT, UPDATE, DELETE"
_APPEND_ONLY = "SELECT, INSERT"

_DDL: list[tuple[str, str, str]] = [
    ("cards", _CARDS, _FULL),
    ("card_ranks", _CARD_RANKS, _FULL),
    ("card_events", _CARD_EVENTS, _APPEND_ONLY),
    ("saved_views", _SAVED_VIEWS, _FULL),
    ("sprints", _SPRINTS, _FULL),
    ("sprint_cards", _SPRINT_CARDS, _FULL),
    ("retros", _RETROS, _FULL),
    ("triage_batches", _TRIAGE_BATCHES, _FULL),
    ("triage_cards", _TRIAGE_CARDS, _FULL),
    ("audit_events", _AUDIT_EVENTS, _APPEND_ONLY),
]


def upgrade() -> None:
    # Runtime role: cluster-level, so create idempotently. NOLOGIN here;
    # credentials are attached by deploy provisioning, never by a migration.
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS "
        "(SELECT 1 FROM pg_roles WHERE rolname = 'cards_app') THEN "
        "CREATE ROLE cards_app NOLOGIN NOSUPERUSER NOBYPASSRLS; "
        "END IF; END $$"
    )

    for table, ddl, privileges in _DDL:
        op.execute(ddl)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_rls ON {table} "
            "USING (org_id = current_setting('app.current_org', true)) "
            "WITH CHECK (org_id = current_setting('app.current_org', true))"
        )
        op.execute(f"GRANT {privileges} ON {table} TO cards_app")

    op.execute("CREATE INDEX card_events_card_idx ON card_events (org_id, card_id, id)")
    op.execute("CREATE INDEX audit_events_org_idx ON audit_events (org_id, id)")
    op.execute("CREATE INDEX cards_org_status_idx ON cards (org_id, status)")

    op.execute("GRANT USAGE ON SCHEMA public TO cards_app")
    # Identity columns draw from sequences owned by the schema owner.
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO cards_app")


def downgrade() -> None:
    for table, _, _ in reversed(_DDL):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    # The cards_app role is cluster-level and may be shared; leave it.
