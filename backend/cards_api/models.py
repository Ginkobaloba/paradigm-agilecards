"""SQLAlchemy declarative models.

Conventions (ADR-2026-07-16):

- Every tenant table carries ``org_id TEXT NOT NULL`` and gets ENABLE + FORCE
  ROW LEVEL SECURITY plus an org policy in its migration. A test
  (``test_rls_postgres.py``) asserts no org_id-bearing table ships without RLS.
- ``org_id`` is the opaque, IdP-verified org identifier from the JWT. There is
  deliberately no local ``orgs`` registry table: the Paradigm IdP is the source
  of truth for org existence, and a local mirror would just be a second copy
  that can drift.
- ``audit_events`` is append-only: the app role holds INSERT/SELECT only, and a
  trigger rejects UPDATE/DELETE even for the owner (see migration 0001).
- Wire shapes preserve the legacy Express contract exactly (see the parity spec
  in the K-P1 handoff): cards serialize as ``{id, file, status, frontmatter,
  mtimeMs[, body]}`` where ``file`` is now a synthetic stable path-shaped key
  and ``mtimeMs`` derives from ``updated_at``.
- Sprint ``starts_at``/``ends_at``/``archived_at`` are TEXT on purpose: the
  legacy contract validates and compares them as ISO *strings* (SQLite TEXT),
  and round-tripping through timestamptz would rewrite date-only values the
  frontend sent verbatim.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# The five board statuses, in load-bearing render order (legacy fs/cards.ts).
STATUS_IDS = ("backlog", "active", "awaiting_amendment_review", "done", "blocked")
STATUS_LABELS = {
    "backlog": "Backlog",
    "active": "Active",
    "awaiting_amendment_review": "In Review",
    "done": "Done",
    "blocked": "Blocked",
}
# Legacy stored cards in folders; the synthetic `file` key keeps that shape so
# existing card identities and frontend expectations (non-empty string, used as
# a React key) survive the move to Postgres.
STATUS_FOLDERS = {
    "backlog": "backlog",
    "active": "active",
    "awaiting_amendment_review": "amendments",
    "done": "done",
    "blocked": "blocked",
}


def _epoch_ms(dt: datetime | None) -> float:
    return dt.timestamp() * 1000.0 if dt else 0.0


class Base(DeclarativeBase):
    pass


class Card(Base):
    """A board card. Legacy stored these as markdown files in status folders;
    here ``body`` + ``frontmatter`` are columns and ``status`` is data."""

    __tablename__ = "cards"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="backlog")
    frontmatter: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def file(self) -> str:
        """Synthetic stable file key preserving the legacy folder/name shape."""
        return f"{STATUS_FOLDERS[self.status]}/{self.id}.md"

    def summary_dict(self) -> dict:
        return {
            "id": self.id,
            "file": self.file,
            "status": self.status,
            "frontmatter": self.frontmatter or {},
            "mtimeMs": _epoch_ms(self.updated_at),
        }

    def detail_dict(self) -> dict:
        return {**self.summary_dict(), "body": self.body}


class CardRank(Base):
    """One rank row per card (legacy ``card_rank``). Rank is a float midpoint
    value (base/step 1024) so concurrent clients agree without renumbering."""

    __tablename__ = "card_rank"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "card_id"], ["cards.org_id", "cards.id"], ondelete="CASCADE"
        ),
    )

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    card_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[float] = mapped_column(Double, nullable=False)

    def public_dict(self) -> dict:
        return {"cardId": self.card_id, "status": self.status, "rank": self.rank}


class CardEvent(Base):
    """Card lifecycle events (legacy ``card_events``); powers the timeline."""

    __tablename__ = "card_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    card_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "cardId": self.card_id,
            "type": self.type,
            "at": self.at.isoformat() if self.at else None,
            "details": self.details,
        }


class SavedView(Base):
    """Saved board views. Legacy scoped these to an integer ``token_id``; the
    JWKS model scopes to (org, subject). ``tokenId`` is served as a compat
    constant -- the frontend stores but never reads it."""

    __tablename__ = "saved_views"
    __table_args__ = (UniqueConstraint("org_id", "owner_sub", "name"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    owner_sub: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            # Legacy compat: integer token ids do not exist under JWKS.
            "tokenId": 0,
            "name": self.name,
            "payload": self.payload,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


SPRINT_STATUSES = ("planning", "active", "completed", "cancelled")


class Sprint(Base):
    __tablename__ = "sprints"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[str] = mapped_column(Text, nullable=False)
    ends_at: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="planning")
    points_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dollar_target: Mapped[float | None] = mapped_column(Double, nullable=True)
    review_hours_target: Mapped[float | None] = mapped_column(Double, nullable=True)
    archived_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "startsAt": self.starts_at,
            "endsAt": self.ends_at,
            "goal": self.goal,
            "status": self.status,
            "pointsTarget": self.points_target,
            "dollarTarget": self.dollar_target,
            "reviewHoursTarget": self.review_hours_target,
            "archivedAt": self.archived_at,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }


class SprintCard(Base):
    __tablename__ = "sprint_cards"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sprint_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[str] = mapped_column(Text, primary_key=True)
    planned_points: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def public_dict(self) -> dict:
        return {
            "sprintId": self.sprint_id,
            "cardId": self.card_id,
            "plannedPoints": self.planned_points,
        }


class Retro(Base):
    """Retros: schema-live, frontend placeholder (legacy parity; snake_case wire)."""

    __tablename__ = "retros"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    sprint_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    held_on: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "sprint_id": self.sprint_id,
            "held_on": self.held_on,
            "summary": self.summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


BATCH_STATES = ("planning", "ready", "promoted", "cancelled")
STAGED_STATES = ("staged", "promoted", "declined")


class StoryBatch(Base):
    """A submit-story planning batch. Legacy modeled this as a ``_staging/``
    directory tree + an in-memory pending map with a 1h TTL; here the batch is
    a row and ``expires_at`` makes the TTL durable."""

    __tablename__ = "story_batches"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    story: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="planning")
    manifest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StagedCard(Base):
    """A planned card awaiting triage/approval (legacy ``_staging/<batch>/*.md``)."""

    __tablename__ = "staged_cards"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "batch_id"],
            ["story_batches.org_id", "story_batches.batch_id"],
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    file: Mapped[str] = mapped_column(Text, primary_key=True)  # staged basename, e.g. b001-01.md
    card_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    frontmatter: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str] = mapped_column(Text, nullable=False, default="staged")
    ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def triage_dict(self) -> dict:
        fm = self.frontmatter or {}
        depends = fm.get("depends_on")
        return {
            "id": self.card_id,
            "title": self.title,
            "file": self.file,
            "bodyExcerpt": (self.body or "")[:280],
            "tier": fm.get("points") if isinstance(fm.get("points"), int) else None,
            "model": fm.get("model") if isinstance(fm.get("model"), str) else None,
            "estimatedTokens": (
                fm.get("estimated_tokens")
                if isinstance(fm.get("estimated_tokens"), int | float)
                else None
            ),
            "dependsOn": [d for d in depends if isinstance(d, str)]
            if isinstance(depends, list)
            else [],
        }


class AuditEvent(Base):
    """One security-relevant event. ``org_id`` is NULL only for pre-auth events
    (e.g. a token that failed verification -- there is no *verified* org to
    attribute it to). NULL-org rows are operator-only: the org-scoped SELECT
    policy never returns them through the API."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    org_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    actor_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts.isoformat() if self.ts else None,
            "org_id": self.org_id,
            "actor_sub": self.actor_sub,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "detail": self.detail,
        }
