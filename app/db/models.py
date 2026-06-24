"""
SQLAlchemy ORM models for LeadFlow's persistent store.

LeadRow/EventRow/CounselorRow mirror the Sheet tabs (Leads / Counselors /
Events) that app/adapters/db_store.py projects state into. ProcessedWebhook
backs idempotency for inbound webhooks (payment, call-result).

outreach_state/outreach_started_at/outreach_attempts on LeadRow are
durability bookkeeping for the BackgroundTasks reconciler (app/reconciler.py)
- they are not part of the Lead domain dataclass (app/models.py) and are
read/written directly against this table, not through SheetStore.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time import now_utc_iso


class LeadRow(Base):
    __tablename__ = "leads"

    lead_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    phone: Mapped[str]
    preferred_language: Mapped[str]
    source: Mapped[str]
    email: Mapped[str | None]
    raw_notes: Mapped[str] = mapped_column(default="")
    status: Mapped[str]
    score: Mapped[int | None]
    assigned_counselor: Mapped[str | None]
    counselor_slot: Mapped[str | None]
    call_result: Mapped[str | None]
    needs_captured: Mapped[str] = mapped_column(default="")
    timestamp_created: Mapped[str] = mapped_column(default=now_utc_iso)
    last_updated: Mapped[str] = mapped_column(default=now_utc_iso)

    # Outreach durability bookkeeping - see module docstring.
    outreach_state: Mapped[str] = mapped_column(default="PENDING")
    outreach_started_at: Mapped[datetime | None]
    outreach_attempts: Mapped[int] = mapped_column(default=0)


class EventRow(Base):
    __tablename__ = "events"

    event_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lead_id: Mapped[str]
    timestamp: Mapped[str] = mapped_column(default=now_utc_iso)
    event_type: Mapped[str]
    details: Mapped[str] = mapped_column(default="")


class CounselorRow(Base):
    __tablename__ = "counselors"

    name: Mapped[str] = mapped_column(primary_key=True)
    languages_spoken: Mapped[list] = mapped_column(JSON, default=list)
    available_slots: Mapped[list] = mapped_column(JSON, default=list)
    current_load: Mapped[int] = mapped_column(default=0)


class ProcessedWebhook(Base):
    __tablename__ = "processed_webhooks"
    __table_args__ = (
        UniqueConstraint("webhook_type", "external_id", name="uq_webhook_type_external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    webhook_type: Mapped[str]
    external_id: Mapped[str]
    processed_at: Mapped[str] = mapped_column(default=now_utc_iso)
