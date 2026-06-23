"""
DB-backed SheetStore implementation.

SYSTEM OF RECORD: This module (DbStore) is the authoritative source of truth for
lead state, counselor assignment, and idempotency. Google Sheets is a human-facing
projection — useful for human operators to review and act on leads, but never
queried by the pipeline for decisions.

DbStore is used in production (app/main.py).
InMemorySheet (app/adapters/sheets.py) is for unit tests only.
GoogleSheet (app/adapters/sheets.py) is a real-integration stub.

Thread-safety note:
    All SheetStore methods are synchronous on the interface. DbStore bridges
    to async SQLAlchemy by running each coroutine in a fresh asyncio event
    loop via asyncio.run(). This is correct because:
    - In FastAPI, sync route functions (and sync BackgroundTask shims) run in
      a thread pool, so there is no running loop in that thread.
    - asyncio.run() creates and destroys a loop per call, which is safe.
    - The SQLAlchemy async engine is safe to use from multiple threads as long
      as each thread uses its own event loop.

Design note on async_session import:
    We import app.db.engine lazily (inside each async method) rather than at
    module top level so that test fixtures can patch engine_module.async_session
    *after* this module is imported without binding to the original session.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.adapters.base import SheetStore
from app.db.models import CounselorRow, EventRow, LeadRow, ProcessedWebhook
from app.models import CallResult, Language, Lead, LeadStatus, Source
from app.utils.time import now_utc_iso


def _get_session():
    """Return the current async_session factory (allows test-time patching)."""
    import app.db.engine as engine_module
    return engine_module.async_session


class DbStore(SheetStore):
    """Async SQLAlchemy-backed SheetStore.

    Each public method creates and disposes its own asyncio event loop so it
    is safe to call from any sync context (FastAPI thread pool, scripts, tests).
    """

    # ------------------------------------------------------------------
    # SheetStore interface
    # ------------------------------------------------------------------

    def append_lead(self, lead: Lead) -> None:
        asyncio.run(self._append_lead_async(lead))

    async def _append_lead_async(self, lead: Lead) -> None:
        async with _get_session()() as session:
            row = LeadRow(
                lead_id=lead.lead_id,
                name=lead.name,
                phone=lead.phone,
                preferred_language=lead.preferred_language.value,
                source=lead.source.value,
                email=lead.email,
                raw_notes=lead.raw_notes,
                status=lead.status.value,
                score=lead.score,
                assigned_counselor=lead.assigned_counselor,
                counselor_slot=lead.counselor_slot,
                call_result=lead.call_result.value if lead.call_result else None,
                needs_captured=lead.needs_captured,
                timestamp_created=lead.timestamp_created,
                last_updated=lead.last_updated,
                outreach_state="PENDING",
                outreach_attempts=0,
            )
            session.add(row)
            await session.commit()

    def update_lead(self, lead: Lead) -> None:
        asyncio.run(self._update_lead_async(lead))

    async def _update_lead_async(self, lead: Lead) -> None:
        async with _get_session()() as session:
            row = await session.get(LeadRow, lead.lead_id)
            if row is None:
                return
            row.name = lead.name
            row.phone = lead.phone
            row.preferred_language = lead.preferred_language.value
            row.source = lead.source.value
            row.email = lead.email
            row.raw_notes = lead.raw_notes
            row.status = lead.status.value
            row.score = lead.score
            row.assigned_counselor = lead.assigned_counselor
            row.counselor_slot = lead.counselor_slot
            row.call_result = lead.call_result.value if lead.call_result else None
            row.needs_captured = lead.needs_captured
            row.last_updated = now_utc_iso()
            await session.commit()

    def get_lead(self, lead_id: str) -> Lead | None:
        return asyncio.run(self._get_lead_async(lead_id))

    async def _get_lead_async(self, lead_id: str) -> Lead | None:
        async with _get_session()() as session:
            row = await session.get(LeadRow, lead_id)
            if row is None:
                return None
            return _row_to_lead(row)

    def quarantine(self, row: dict, error: str) -> None:
        asyncio.run(self._quarantine_async(row, error))

    async def _quarantine_async(self, row: dict, error: str) -> None:
        async with _get_session()() as session:
            event = EventRow(
                lead_id="__quarantine__",
                event_type="quarantined",
                details=f"{error} | raw={row}",
            )
            session.add(event)
            await session.commit()

    def log_event(self, lead_id: str, event_type: str, details: str = "") -> None:
        asyncio.run(self._log_event_async(lead_id, event_type, details))

    async def _log_event_async(self, lead_id: str, event_type: str, details: str) -> None:
        async with _get_session()() as session:
            event = EventRow(lead_id=lead_id, event_type=event_type, details=details)
            session.add(event)
            await session.commit()

    def get_counselors(self) -> list[dict]:
        return asyncio.run(self._get_counselors_async())

    async def _get_counselors_async(self) -> list[dict]:
        async with _get_session()() as session:
            result = await session.execute(select(CounselorRow))
            rows = result.scalars().all()
            return [_counselor_row_to_dict(r) for r in rows]

    def update_counselor(self, counselor: dict) -> None:
        asyncio.run(self._update_counselor_async(counselor))

    async def _update_counselor_async(self, counselor: dict) -> None:
        async with _get_session()() as session:
            row = await session.get(CounselorRow, counselor["name"])
            if row is None:
                return
            row.languages_spoken = counselor.get("languages_spoken", [])
            row.available_slots = counselor.get("available_slots", [])
            row.current_load = counselor.get("current_load", 0)
            await session.commit()

    def record_processed_webhook(self, webhook_type: str, external_id: str) -> bool:
        """Atomically record a webhook as processed.

        Returns True if this is the first time we see (webhook_type, external_id).
        Returns False if it was already recorded (duplicate — caller should skip).
        Uses a unique constraint + INSERT to guarantee atomicity even under
        concurrent requests.
        """
        return asyncio.run(
            self._record_processed_webhook_async(webhook_type, external_id)
        )

    async def _record_processed_webhook_async(
        self, webhook_type: str, external_id: str
    ) -> bool:
        async with _get_session()() as session:
            entry = ProcessedWebhook(webhook_type=webhook_type, external_id=external_id)
            session.add(entry)
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False


# ------------------------------------------------------------------
# Private converters
# ------------------------------------------------------------------

def _row_to_lead(row: LeadRow) -> Lead:
    return Lead(
        lead_id=row.lead_id,
        name=row.name,
        phone=row.phone,
        preferred_language=Language(row.preferred_language),
        source=Source(row.source),
        email=row.email,
        raw_notes=row.raw_notes,
        status=LeadStatus(row.status),
        score=row.score,
        assigned_counselor=row.assigned_counselor,
        counselor_slot=row.counselor_slot,
        call_result=CallResult(row.call_result) if row.call_result else None,
        needs_captured=row.needs_captured,
        timestamp_created=row.timestamp_created,
        last_updated=row.last_updated,
    )


def _counselor_row_to_dict(row: CounselorRow) -> dict:
    return {
        "name": row.name,
        "languages_spoken": row.languages_spoken,
        "available_slots": list(row.available_slots),
        "current_load": row.current_load,
    }
