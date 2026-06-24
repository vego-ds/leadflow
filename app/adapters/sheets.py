"""
Google Sheets adapter.

InMemorySheet - demo stand-in. Holds the four tabs in memory so the pipeline
                runs with no Google credentials. Mirrors the real tab layout:
                Leads / Needs Review / Counselors / Events.
GoogleSheet   - real integration stub (gspread / Sheets API) for going live.

The pipeline only depends on the SheetStore interface, so swapping is clean.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import gspread
from google.oauth2.service_account import Credentials

from app.adapters.base import SheetStore
from app.models import CallResult, Language, Lead, LeadStatus, Source
from app.utils.logging import log
from app.utils.time import now_utc_iso


class InMemorySheet(SheetStore):
    def __init__(self):
        self.leads: dict[str, dict] = {}     # Leads tab
        self.needs_review: list[dict] = []   # Needs Review tab
        self.counselors: list[dict] = []     # Counselors tab
        self.events: list[dict] = []         # Events tab
        self._event_seq = 0
        self._processed_webhooks: set[tuple[str, str]] = set()

    def seed_counselors(self, roster: list[dict]) -> None:
        """Load the starting counselor roster. Demo/startup use only."""
        self.counselors = roster

    def append_lead(self, lead: Lead) -> None:
        self.leads[lead.lead_id] = lead.to_row()

    def update_lead(self, lead: Lead) -> None:
        lead.last_updated = now_utc_iso()
        self.leads[lead.lead_id] = lead.to_row()

    def get_lead(self, lead_id: str) -> Lead | None:
        row = self.leads.get(lead_id)
        if row is None:
            return None
        return Lead(
            lead_id=row["lead_id"],
            name=row["name"],
            phone=row["phone"],
            preferred_language=Language(row["preferred_language"]),
            source=Source(row["source"]),
            email=row["email"],
            raw_notes=row["raw_notes"],
            status=LeadStatus(row["status"]),
            score=row["score"],
            assigned_counselor=row["assigned_counselor"],
            counselor_slot=row["counselor_slot"],
            call_result=CallResult(row["call_result"]) if row["call_result"] else None,
            needs_captured=row["needs_captured"],
            timestamp_created=row["timestamp_created"],
            last_updated=row["last_updated"],
        )

    def quarantine(self, row: dict, error: str) -> None:
        self.needs_review.append({**row, "validation_error": error})

    def log_event(self, lead_id: str, event_type: str, details: str = "") -> None:
        self._event_seq += 1
        self.events.append({
            "event_id": self._event_seq,
            "lead_id": lead_id,
            "timestamp": now_utc_iso(),
            "event_type": event_type,
            "details": details,
        })

    def get_counselors(self) -> list[dict]:
        return self.counselors

    def update_counselor(self, counselor: dict) -> None:
        for i, existing in enumerate(self.counselors):
            if existing["name"] == counselor["name"]:
                self.counselors[i] = counselor
                return

    def record_processed_webhook(self, webhook_type: str, external_id: str) -> bool:
        key = (webhook_type, external_id)
        if key in self._processed_webhooks:
            return False
        self._processed_webhooks.add(key)
        return True


class GoogleSheet(SheetStore):
    """
    Real Google Sheets integration via gspread + a service-account key.

    Sheets is a human-facing projection only (see app/adapters/db_store.py
    and app/adapters/projection_store.py) — the pipeline never reads from it
    for decisions, so failures here are logged and swallowed rather than
    raised. Method signatures stay identical to InMemorySheet.

    All methods are synchronous, matching the SheetStore interface — every
    call site is already off the FastAPI event loop (thread pool / scripts /
    background tasks), the same invariant DbStore relies on. The one
    exception is aclose(), which is awaited from the async lifespan and
    offloads its blocking flush to a thread.
    """

    EVENT_BUFFER_MAX = 20
    EVENT_FLUSH_INTERVAL_SECONDS = 5.0

    LEADS_TAB = "Leads"
    NEEDS_REVIEW_TAB = "Needs Review"
    COUNSELORS_TAB = "Counselors"
    EVENTS_TAB = "Events"

    # Column order must match the real Sheet's header row exactly — writes
    # are positional (append_row/update by column letter), not header-driven.
    LEAD_COLUMNS = (
        "lead_id", "timestamp_created", "name", "phone", "email", "source",
        "preferred_language", "raw_notes", "status", "score",
        "assigned_counselor", "counselor_slot", "call_result",
        "needs_captured", "last_updated",
    )
    # Needs Review mirrors the full Leads layout plus a trailing
    # validation_error column (it's quarantine, not just a pre-ingest list).
    NEEDS_REVIEW_COLUMNS = LEAD_COLUMNS + ("validation_error",)
    # Column A (counselor_id) is sheet-managed; the app has no counselor_id
    # concept (counselors are identified by name) — writes skip column A.
    COUNSELOR_COLUMNS = ("name", "languages_spoken", "available_slots", "current_load")
    COUNSELOR_NAME_COL = 2

    _SCOPES = (
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    )

    def __init__(self, spreadsheet_id: str, creds_path: str):
        self.spreadsheet_id = spreadsheet_id
        self.creds_path = creds_path
        self._spreadsheet: gspread.Spreadsheet | None = None
        self._worksheets: dict[str, gspread.Worksheet] = {}
        self._lead_row_cache: dict[str, int] = {}
        self._event_buffer: list[list[str]] = []
        self._last_flush = time.monotonic()

    # ------------------------------------------------------------------
    # Connection — lazy, so constructing the adapter never does network I/O.
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        if self._spreadsheet is not None:
            return
        creds = Credentials.from_service_account_file(self.creds_path, scopes=self._SCOPES)
        client = gspread.authorize(creds)
        self._spreadsheet = client.open_by_key(self.spreadsheet_id)

    def _worksheet(self, tab_name: str) -> gspread.Worksheet:
        self._connect()
        if tab_name not in self._worksheets:
            self._worksheets[tab_name] = self._spreadsheet.worksheet(tab_name)
        return self._worksheets[tab_name]

    # ------------------------------------------------------------------
    # Leads tab
    # ------------------------------------------------------------------

    def append_lead(self, lead: Lead) -> None:
        self._lead_row_cache.pop(lead.lead_id, None)
        row = lead.to_row()
        values = [_cell(row[col]) for col in self.LEAD_COLUMNS]
        try:
            self._worksheet(self.LEADS_TAB).append_row(values)
        except gspread.exceptions.APIError as exc:
            log(f"[sheets] append_lead failed for lead_id={lead.lead_id}: {exc}")

    def update_lead(self, lead: Lead) -> None:
        try:
            row_index = self._find_lead_row(lead.lead_id)
            if row_index is None:
                log(f"[sheets] update_lead: lead_id={lead.lead_id} not found, skipping")
                return
            row = lead.to_row()
            values = [_cell(row[col]) for col in self.LEAD_COLUMNS]
            last_col = _column_letter(len(self.LEAD_COLUMNS))
            self._worksheet(self.LEADS_TAB).update(f"A{row_index}:{last_col}{row_index}", [values])
        except gspread.exceptions.APIError as exc:
            log(f"[sheets] update_lead failed for lead_id={lead.lead_id}: {exc}")

    def get_lead(self, lead_id: str) -> Lead | None:
        try:
            row_index = self._find_lead_row(lead_id)
            if row_index is None:
                return None
            values = self._worksheet(self.LEADS_TAB).row_values(row_index)
        except gspread.exceptions.APIError as exc:
            log(f"[sheets] get_lead failed for lead_id={lead_id}: {exc}")
            return None
        return _values_to_lead(self.LEAD_COLUMNS, values)

    def _find_lead_row(self, lead_id: str) -> int | None:
        """Return the 1-indexed Sheet row for lead_id.

        Checks the in-memory cache first; on a miss, scans the lead_id
        column once and caches the result so repeated lookups (e.g. one
        update_lead per status transition) don't re-scan the sheet.
        """
        if lead_id in self._lead_row_cache:
            return self._lead_row_cache[lead_id]

        lead_id_column = self._worksheet(self.LEADS_TAB).col_values(1)
        for i, value in enumerate(lead_id_column, start=1):
            if value == lead_id:
                self._lead_row_cache[lead_id] = i
                return i
        return None

    # ------------------------------------------------------------------
    # Needs Review tab
    # ------------------------------------------------------------------

    def quarantine(self, row: dict, error: str) -> None:
        values = [str(row.get(col, "") or "") for col in self.NEEDS_REVIEW_COLUMNS[:-1]]
        values.append(error)
        try:
            self._worksheet(self.NEEDS_REVIEW_TAB).append_row(values)
        except gspread.exceptions.APIError as exc:
            log(f"[sheets] quarantine failed: {exc}")

    # ------------------------------------------------------------------
    # Events tab — buffered, flushed in batches (write volume is the
    # highest of any tab, and events are pure activity log, not state).
    # ------------------------------------------------------------------

    def log_event(self, lead_id: str, event_type: str, details: str = "") -> None:
        # event_id is a uuid (not a local counter) so it stays unique across
        # process restarts — nothing here persists a sequence across runs.
        event_id = uuid.uuid4().hex
        self._event_buffer.append([event_id, lead_id, now_utc_iso(), event_type, details])
        stale = time.monotonic() - self._last_flush >= self.EVENT_FLUSH_INTERVAL_SECONDS
        if len(self._event_buffer) >= self.EVENT_BUFFER_MAX or stale:
            self._flush_events()

    def _flush_events(self) -> None:
        if not self._event_buffer:
            self._last_flush = time.monotonic()
            return
        try:
            self._worksheet(self.EVENTS_TAB).append_rows(self._event_buffer)
            self._event_buffer = []
        except gspread.exceptions.APIError as exc:
            log(
                f"[sheets] event flush failed, {len(self._event_buffer)} "
                f"event(s) retained for retry: {exc}"
            )
        finally:
            self._last_flush = time.monotonic()

    async def aclose(self) -> None:
        """Flush any buffered events. Called once during app shutdown.

        The only SheetStore-adjacent call that actually runs on the event
        loop (awaited from main.py's lifespan) — every other method here
        is invoked from contexts already off-loop, so this is the one spot
        where asyncio.to_thread is the right tool.
        """
        await asyncio.to_thread(self._flush_events)

    # ------------------------------------------------------------------
    # Counselors tab
    # ------------------------------------------------------------------

    def get_counselors(self) -> list[dict]:
        try:
            records = self._worksheet(self.COUNSELORS_TAB).get_all_records()
        except gspread.exceptions.APIError as exc:
            log(f"[sheets] get_counselors failed: {exc}")
            return []
        return [_record_to_counselor(r) for r in records]

    def update_counselor(self, counselor: dict) -> None:
        try:
            worksheet = self._worksheet(self.COUNSELORS_TAB)
            names = worksheet.col_values(self.COUNSELOR_NAME_COL)
            row_index = next(
                (i for i, name in enumerate(names, start=1) if name == counselor["name"]),
                None,
            )
            if row_index is None:
                log(f"[sheets] update_counselor: {counselor['name']!r} not found, skipping")
                return
            values = [
                counselor["name"],
                ",".join(counselor.get("languages_spoken", [])),
                ",".join(counselor.get("available_slots", [])),
                counselor.get("current_load", 0),
            ]
            start_col = _column_letter(self.COUNSELOR_NAME_COL)
            end_col = _column_letter(self.COUNSELOR_NAME_COL + len(self.COUNSELOR_COLUMNS) - 1)
            worksheet.update(f"{start_col}{row_index}:{end_col}{row_index}", [values])
        except gspread.exceptions.APIError as exc:
            log(f"[sheets] update_counselor failed for {counselor.get('name')}: {exc}")

    # ------------------------------------------------------------------
    # Idempotency is DB-only — Sheets is a projection, not a source of truth.
    # ------------------------------------------------------------------

    def record_processed_webhook(self, webhook_type: str, external_id: str) -> bool:
        raise NotImplementedError(
            "DB is the source of truth for idempotency; not implemented on Sheets projection"
        )


# ---------------------------------------------------------------------------
# Private converters
# ---------------------------------------------------------------------------

def _cell(value: object) -> object:
    return "" if value is None else value


def _column_letter(n: int) -> str:
    """1-indexed column number -> spreadsheet column letter (1 -> 'A')."""
    letters = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _values_to_lead(columns: tuple[str, ...], values: list[str]) -> Lead:
    padded = list(values) + [""] * (len(columns) - len(values))
    row = dict(zip(columns, padded))
    return Lead(
        lead_id=row["lead_id"],
        name=row["name"],
        phone=row["phone"],
        preferred_language=Language(row["preferred_language"]),
        source=Source(row["source"]),
        email=row["email"] or None,
        raw_notes=row["raw_notes"],
        status=LeadStatus(row["status"]),
        score=int(row["score"]) if row["score"] else None,
        assigned_counselor=row["assigned_counselor"] or None,
        counselor_slot=row["counselor_slot"] or None,
        call_result=CallResult(row["call_result"]) if row["call_result"] else None,
        needs_captured=row["needs_captured"],
        timestamp_created=row["timestamp_created"],
        last_updated=row["last_updated"],
    )


def _record_to_counselor(record: dict) -> dict:
    return {
        "name": record.get("name", ""),
        "languages_spoken": _split_csv(record.get("languages_spoken", "")),
        "available_slots": _split_csv(record.get("available_slots", "")),
        "current_load": int(record.get("current_load") or 0),
    }


def _split_csv(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [v.strip() for v in str(value).split(",") if v.strip()]
