"""
ProjectionStore — composes the DB (system of record) with an optional
Google Sheets projection.

Writes go to the DB always, and mirror to Sheets if configured; reads and
idempotency checks always come from the DB, since Sheets is a human-facing
projection the pipeline never queries for decisions (see
app/adapters/db_store.py). A Sheets mirror failure never breaks the
pipeline — it's logged by the Sheets adapter itself and swallowed here too,
as a second line of defense.
"""
from __future__ import annotations

from typing import Callable

from app.adapters.base import SheetStore
from app.adapters.sheets import GoogleSheet
from app.models import Lead
from app.utils.logging import log


class ProjectionStore(SheetStore):
    """Wraps a DB-backed SheetStore with an optional GoogleSheet mirror.

    If `sheet` is None, this behaves exactly like `db` alone.
    """

    def __init__(self, db: SheetStore, sheet: GoogleSheet | None = None):
        self._db = db
        self._sheet = sheet

    def _mirror(self, method: Callable[..., None], *args: object) -> None:
        try:
            method(*args)
        except Exception as exc:  # noqa: BLE001 — a Sheets glitch must never break the pipeline
            log(f"[projection_store] Sheets mirror failed: {exc}")

    # ------------------------------------------------------------------
    # Writes — DB always, Sheets mirrored best-effort
    # ------------------------------------------------------------------

    def append_lead(self, lead: Lead) -> None:
        self._db.append_lead(lead)
        if self._sheet:
            self._mirror(self._sheet.append_lead, lead)

    def update_lead(self, lead: Lead) -> None:
        self._db.update_lead(lead)
        if self._sheet:
            self._mirror(self._sheet.update_lead, lead)

    def quarantine(self, row: dict, error: str) -> None:
        self._db.quarantine(row, error)
        if self._sheet:
            self._mirror(self._sheet.quarantine, row, error)

    def log_event(self, lead_id: str, event_type: str, details: str = "") -> None:
        self._db.log_event(lead_id, event_type, details)
        if self._sheet:
            self._mirror(self._sheet.log_event, lead_id, event_type, details)

    def update_counselor(self, counselor: dict) -> None:
        self._db.update_counselor(counselor)
        if self._sheet:
            self._mirror(self._sheet.update_counselor, counselor)

    # ------------------------------------------------------------------
    # Reads and idempotency — DB only, Sheets is never the source of truth
    # ------------------------------------------------------------------

    def get_lead(self, lead_id: str) -> Lead | None:
        return self._db.get_lead(lead_id)

    def get_counselors(self) -> list[dict]:
        return self._db.get_counselors()

    def record_processed_webhook(self, webhook_type: str, external_id: str) -> bool:
        return self._db.record_processed_webhook(webhook_type, external_id)

    # ------------------------------------------------------------------
    # Shutdown — flush whatever the Sheets mirror has buffered
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        if self._sheet is not None:
            await self._sheet.aclose()
