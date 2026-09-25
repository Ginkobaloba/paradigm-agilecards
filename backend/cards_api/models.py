"""SQLAlchemy ORM models. The schema of record is the Alembic migration
(``migrations/versions/0001_initial_schema_rls.py``) — these classes mirror it
for query building. The pg test suite runs against the migrated schema, so any
drift between the two surfaces as a failing test, not a silent mismatch.

Serialization note: wire shapes (camelCase, ``mtimeMs`` floats, ISO strings)
live in the routers/schemas layer, not here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Float, ForeignKey, ForeignKeyConstraint, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

STATUS_IDS = ("backlog", "active", "awaiting_amendment_review", "done", "blocked")
STATUS_LABELS = {
    "backlog": "Backlog",
    "active": "Active",
    "awaiting_amendment_review": "In Review",
    "done": "Done",
    "blocked": "Blocked",
}
SPRINT_STATUSES = ("planning", "active", "completed", "cancelled")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CardRow(Base):
    __tablename__ = "cards"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    file: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    frontmatter: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CardRankRow(Base):
    __tablename__ = "card_ranks"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    card_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CardEventRow(Base):
    __tablename__ = "card_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    card_id: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(nullable=False)
    details: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SavedViewRow(Base):
    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    owner_sub: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SprintRow(Base):
    __tablename__ = "sprints"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[str] = mapped_column(Text, nullable=False)
    ends_at: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="planning")
    points_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dollar_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_hours_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    archived_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SprintCardRow(Base):
    __tablename__ = "sprint_cards"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sprint_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sprints.id", ondelete="CASCADE"), primary_key=True
    )
    card_id: Mapped[str] = mapped_column(Text, primary_key=True)
    planned_points: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RetroRow(Base):
    __tablename__ = "retros"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    sprint_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sprints.id", ondelete="SET NULL"), nullable=True
    )
    held_on: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TriageBatchRow(Base):
    __tablename__ = "triage_batches"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    story: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, default="ready")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TriageCardRow(Base):
    __tablename__ = "triage_cards"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "batch_id"],
            ["triage_batches.org_id", "triage_batches.batch_id"],
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    file: Mapped[str] = mapped_column(Text, primary_key=True)
    card_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    frontmatter: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    body: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(Text, default="staged")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
