"""
Google Sheets adapter.

InMemorySheet - demo stand-in. Holds the four tabs in memory so the pipeline
                runs with no Google credentials. Mirrors the real tab layout:
                Leads / Needs Review / Counselors / Events.
GoogleSheet   - real integration stub (gspread / Sheets API) for going live.

The pipeline only depends on the SheetStore interface, so swapping is clean.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.models import Lead


class SheetStore(ABC):
    @abstractmethod
    def append_lead(self, lead: Lead) -> None: ...

    @abstractmethod
    def update_lead(self, lead: Lead) -> None: ...

    @abstractmethod
    def quarantine(self, row: dict, error: str) -> None: ...

    @abstractmethod
    def log_event(self, lead_id: str, event_type: str, details: str = "") -> None: ...


class InMemorySheet(SheetStore):
    def __init__(self):
        self.leads: dict[str, dict] = {}     # Leads tab
        self.needs_review: list[dict] = []   # Needs Review tab
        self.events: list[dict] = []         # Events tab
        self._event_seq = 0

    def append_lead(self, lead: Lead) -> None:
        self.leads[lead.lead_id] = lead.to_row()

    def update_lead(self, lead: Lead) -> None:
        lead.last_updated = datetime.utcnow().isoformat()
        self.leads[lead.lead_id] = lead.to_row()

    def quarantine(self, row: dict, error: str) -> None:
        self.needs_review.append({**row, "validation_error": error})

    def log_event(self, lead_id: str, event_type: str, details: str = "") -> None:
        self._event_seq += 1
        self.events.append({
            "event_id": self._event_seq,
            "lead_id": lead_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "details": details,
        })


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

    def quarantine(self, row: dict, error: str) -> None:
        raise NotImplementedError

    def log_event(self, lead_id: str, event_type: str, details: str = "") -> None:
        raise NotImplementedError
