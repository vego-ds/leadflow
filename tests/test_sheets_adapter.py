"""
GoogleSheet adapter tests. gspread.authorize and the Spreadsheet/Worksheet
objects are mocked — no network calls, no real credentials.

Run: pytest -q tests/test_sheets_adapter.py
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import gspread.exceptions
import pytest

from app.adapters.sheets import GoogleSheet
from app.models import Language, Lead, LeadStatus, Source


def _make_lead(lead_id: str = "abc123") -> Lead:
    return Lead(
        lead_id=lead_id,
        name="Test User",
        phone="+919876543210",
        preferred_language=Language.HINDI,
        source=Source.WEBSITE,
        email="t@example.com",
        status=LeadStatus.NEW,
    )


@pytest.fixture()
def google_sheet(monkeypatch):
    """A GoogleSheet wired to mocked gspread Worksheet objects per tab."""
    worksheets = {
        "Leads": MagicMock(name="LeadsWorksheet"),
        "Needs Review": MagicMock(name="NeedsReviewWorksheet"),
        "Counselors": MagicMock(name="CounselorsWorksheet"),
        "Events": MagicMock(name="EventsWorksheet"),
    }
    fake_spreadsheet = MagicMock(name="Spreadsheet")
    fake_spreadsheet.worksheet.side_effect = lambda tab: worksheets[tab]

    fake_client = MagicMock(name="Client")
    fake_client.open_by_key.return_value = fake_spreadsheet

    monkeypatch.setattr(
        "app.adapters.sheets.Credentials.from_service_account_file",
        lambda path, scopes: MagicMock(name="Credentials"),
    )
    monkeypatch.setattr("app.adapters.sheets.gspread.authorize", lambda creds: fake_client)

    sheet = GoogleSheet(spreadsheet_id="sheet123", creds_path="creds.json")
    return sheet, worksheets


# ---------------------------------------------------------------------------
# append_lead / write-through mapping
# ---------------------------------------------------------------------------

def test_append_lead_calls_append_row_with_mapped_values(google_sheet):
    sheet, worksheets = google_sheet
    lead = _make_lead("abc123")

    sheet.append_lead(lead)

    worksheets["Leads"].append_row.assert_called_once()
    values = worksheets["Leads"].append_row.call_args[0][0]
    assert values == [
        "abc123", "Test User", "+919876543210", "hi", "website", "t@example.com",
        "", "New", "", "", "", "", "", lead.timestamp_created, lead.last_updated,
    ]


def test_append_lead_swallows_api_error(google_sheet):
    sheet, worksheets = google_sheet
    worksheets["Leads"].append_row.side_effect = gspread.exceptions.APIError(MagicMock())

    sheet.append_lead(_make_lead("abc123"))  # must not raise


def test_quarantine_appends_row_with_validation_error(google_sheet):
    sheet, worksheets = google_sheet

    sheet.quarantine({"name": "", "phone": "123"}, "missing name")

    worksheets["Needs Review"].append_row.assert_called_once()
    values = worksheets["Needs Review"].append_row.call_args[0][0]
    assert values[-1] == "missing name"


def test_record_processed_webhook_raises_not_implemented(google_sheet):
    sheet, _ = google_sheet

    with pytest.raises(NotImplementedError):
        sheet.record_processed_webhook("call_result", "call_1")


# ---------------------------------------------------------------------------
# Lead row cache
# ---------------------------------------------------------------------------

def test_update_lead_caches_row_index_after_first_scan(google_sheet):
    sheet, worksheets = google_sheet
    worksheets["Leads"].col_values.return_value = ["lead_id", "abc123"]
    lead = _make_lead("abc123")

    sheet.update_lead(lead)
    sheet.update_lead(lead)

    worksheets["Leads"].col_values.assert_called_once_with(1)  # scanned only once
    assert worksheets["Leads"].update.call_count == 2
    range_arg = worksheets["Leads"].update.call_args_list[0][0][0]
    assert range_arg.startswith("A2:")  # row 2: header is row 1


def test_append_lead_invalidates_cached_row(google_sheet):
    sheet, worksheets = google_sheet
    sheet._lead_row_cache["abc123"] = 5

    sheet.append_lead(_make_lead("abc123"))

    assert "abc123" not in sheet._lead_row_cache


def test_update_lead_skips_when_row_not_found(google_sheet):
    sheet, worksheets = google_sheet
    worksheets["Leads"].col_values.return_value = ["lead_id"]  # header only, no match

    sheet.update_lead(_make_lead("missing"))

    worksheets["Leads"].update.assert_not_called()


# ---------------------------------------------------------------------------
# log_event batching: flush on size threshold or time interval
# ---------------------------------------------------------------------------

def test_log_event_buffers_until_size_threshold(google_sheet):
    sheet, worksheets = google_sheet

    for i in range(GoogleSheet.EVENT_BUFFER_MAX - 1):
        sheet.log_event("lead1", "called", f"attempt {i}")
    worksheets["Events"].append_rows.assert_not_called()

    sheet.log_event("lead1", "called", "final attempt")  # hits the threshold
    worksheets["Events"].append_rows.assert_called_once()
    flushed = worksheets["Events"].append_rows.call_args[0][0]
    assert len(flushed) == GoogleSheet.EVENT_BUFFER_MAX
    assert sheet._event_buffer == []


def test_log_event_flushes_after_interval_elapses(google_sheet, monkeypatch):
    """Time-based flush, driven by a fake clock — no real sleep, no flakiness."""
    sheet, worksheets = google_sheet
    clock = {"t": 1_000.0}
    monkeypatch.setattr("app.adapters.sheets.time.monotonic", lambda: clock["t"])
    sheet._last_flush = clock["t"]

    sheet.log_event("lead1", "called", "first")
    worksheets["Events"].append_rows.assert_not_called()

    clock["t"] += GoogleSheet.EVENT_FLUSH_INTERVAL_SECONDS + 1
    sheet.log_event("lead1", "called", "second")

    worksheets["Events"].append_rows.assert_called_once()
    assert len(worksheets["Events"].append_rows.call_args[0][0]) == 2


def test_log_event_retains_buffer_on_flush_failure(google_sheet):
    sheet, worksheets = google_sheet
    worksheets["Events"].append_rows.side_effect = gspread.exceptions.APIError(MagicMock())

    for i in range(GoogleSheet.EVENT_BUFFER_MAX):
        sheet.log_event("lead1", "called", f"attempt {i}")

    assert len(sheet._event_buffer) == GoogleSheet.EVENT_BUFFER_MAX  # not lost


def test_aclose_flushes_remaining_events(google_sheet):
    sheet, worksheets = google_sheet
    sheet.log_event("lead1", "called", "pending")
    worksheets["Events"].append_rows.assert_not_called()

    asyncio.run(sheet.aclose())

    worksheets["Events"].append_rows.assert_called_once()
    assert sheet._event_buffer == []


def test_aclose_is_a_noop_when_buffer_is_empty(google_sheet):
    sheet, worksheets = google_sheet

    asyncio.run(sheet.aclose())

    worksheets["Events"].append_rows.assert_not_called()


# ---------------------------------------------------------------------------
# Config validation — exactly one of GOOGLE_CREDS_PATH/SPREADSHEET_ID set
# ---------------------------------------------------------------------------

class TestGoogleSheetsConfigValidation:
    def test_only_spreadsheet_id_set_raises(self):
        from app.config import Settings

        with pytest.raises(ValueError, match="must be set together"):
            Settings(spreadsheet_id="sheet123", google_creds_path=None)

    def test_only_creds_path_set_raises(self):
        from app.config import Settings

        with pytest.raises(ValueError, match="must be set together"):
            Settings(spreadsheet_id=None, google_creds_path="creds.json")

    def test_both_set_is_valid(self):
        from app.config import Settings

        settings = Settings(spreadsheet_id="sheet123", google_creds_path="creds.json")
        assert settings.spreadsheet_id == "sheet123"
        assert settings.google_creds_path == "creds.json"

    def test_both_empty_is_valid(self):
        from app.config import Settings

        settings = Settings(spreadsheet_id=None, google_creds_path=None)
        assert settings.spreadsheet_id is None
        assert settings.google_creds_path is None
