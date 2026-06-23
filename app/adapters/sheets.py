"""
Google Sheets adapter.

InMemorySheet - demo stand-in. Holds the four tabs in memory so the pipeline
                runs with no Google credentials. Mirrors the real tab layout:
                Leads / Needs Review / Counselors / Events.
GoogleSheet   - real integration stub (gspread / Sheets API) for going live.

The pipeline only depends on the SheetStore interface, so swapping is clean.
"""
from __future__ import annotations

from app.adapters.base import SheetStore
from app.models import CallResult, Language, Lead, LeadStatus, Source
from app.utils.time import now_utc_iso


class InMemorySheet(SheetStore):
    def __init__(self):
        self.leads: dict[str, dict] = {}     # Leads tab
        self.needs_review: list[dict] = []   # Needs Review tab
        self.counselors: list[dict] = []     # Counselors tab
        self.events: list[dict] = []         # Events tab
        self._event_seq = 0

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


class GoogleSheet(SheetStore):
    """
    Real Google Sheets integration. Not active in the demo.

    To go live: authenticate with a service account, open the spreadsheet by id,
    and implement each method against the matching tab via gspread or the
    Sheets API. Method signatures stay identical to InMemorySheet.
    """

    def __init__(self, spreadsheet_id: str, creds_path: str):
        self.spreadsheet_id = spreadsheet_id
        self.creds_path = creds_path

    def append_lead(self, lead: Lead) -> None:
        raise NotImplementedError("GoogleSheet is a stub. Wire up gspread here.")

    def update_lead(self, lead: Lead) -> None:
        raise NotImplementedError

    def get_lead(self, lead_id: str) -> Lead | None:
        raise NotImplementedError

    def quarantine(self, row: dict, error: str) -> None:
        raise NotImplementedError

    def log_event(self, lead_id: str, event_type: str, details: str = "") -> None:
        raise NotImplementedError

    def get_counselors(self) -> list[dict]:
        raise NotImplementedError("GoogleSheet is a stub. Wire up gspread here.")

    def update_counselor(self, counselor: dict) -> None:
        raise NotImplementedError("GoogleSheet is a stub. Wire up gspread here.")
