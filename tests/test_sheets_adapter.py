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
        "abc123", lead.timestamp_created, "Test User", "+919876543210",
        "t@example.com", "website", "hi", "", "New", "", "", "", "", "",
        lead.last_updated,
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


def test_quarantine_writes_empty_lead_id_for_pre_ingest_failures(google_sheet):
    """A row that failed ingest validation has no lead_id yet — the column
    is written empty, not skipped or misaligned."""
    sheet, worksheets = google_sheet

    sheet.quarantine({"name": "", "phone": "123"}, "missing name")

    values = worksheets["Needs Review"].append_row.call_args[0][0]
    assert values[0] == ""  # lead_id


def test_quarantine_writes_lead_id_for_permanent_outreach_errors(google_sheet):
    """A lead that fails outreach with a permanent error is quarantined via
    its full row (Lead.to_row()), which has a lead_id — it must not be
    silently dropped when writing to the real Needs Review tab."""
    sheet, worksheets = google_sheet
    lead = _make_lead("abc123")

    sheet.quarantine(lead.to_row(), "Outreach failed with permanent error: email")

    values = worksheets["Needs Review"].append_row.call_args[0][0]
    assert values[0] == "abc123"  # lead_id
    assert values[-1] == "Outreach failed with permanent error: email"


def test_update_counselor_skips_counselor_id_column(google_sheet):
    """Column A (counselor_id) is sheet-managed, not part of the app's data
    model — update_counselor must not overwrite it, so lookups and writes
    both skip to column B."""
    sheet, worksheets = google_sheet
    worksheets["Counselors"].col_values.return_value = ["Anita", "Ravi"]

    sheet.update_counselor({
        "name": "Ravi", "languages_spoken": ["hi", "en"],
        "available_slots": ["Mon 10"], "current_load": 2,
    })

    worksheets["Counselors"].col_values.assert_called_once_with(GoogleSheet.COUNSELOR_NAME_COL)
    range_arg, values = worksheets["Counselors"].update.call_args[0]
    assert range_arg.startswith("B2:")
    assert values == [["Ravi", "hi,en", "Mon 10", 2]]


def test_record_processed_webhook_raises_not_implemented(google_sheet):
    sheet, _ = google_sheet

    with pytest.raises(NotImplementedError):
        sheet.record_processed_webhook("call_result", "call_1")


# ---------------------------------------------------------------------------
# Counselors tab — read parsing
# ---------------------------------------------------------------------------

def test_get_counselors_parses_rows_correctly(google_sheet):
    sheet, worksheets = google_sheet
    worksheets["Counselors"].get_all_records.return_value = [
        {
            "counselor_id": "c1", "name": "Anita", "languages_spoken": "hi,en",
            "available_slots": "Mon 10,Tue 11", "current_load": 3,
        },
        {
            "counselor_id": "c2", "name": "Ravi", "languages_spoken": "te",
            "available_slots": "", "current_load": 0,
        },
    ]

    counselors = sheet.get_counselors()

    assert counselors == [
        {"name": "Anita", "languages_spoken": ["hi", "en"],
         "available_slots": ["Mon 10", "Tue 11"], "current_load": 3},
        {"name": "Ravi", "languages_spoken": ["te"],
         "available_slots": [], "current_load": 0},
    ]


def test_get_counselors_swallows_api_error(google_sheet):
    sheet, worksheets = google_sheet
    worksheets["Counselors"].get_all_records.side_effect = gspread.exceptions.APIError(MagicMock())

    counselors = sheet.get_counselors()  # must not raise

    assert counselors == []


# ---------------------------------------------------------------------------
# Init / connection — lazy by design (see GoogleSheet docstring)
# ---------------------------------------------------------------------------

def test_init_with_valid_mock_creds_succeeds(monkeypatch):
    """Construction never touches the network — valid-looking creds (or no
    creds at all) succeed immediately."""
    monkeypatch.setattr(
        "app.adapters.sheets.Credentials.from_service_account_file",
        lambda path, scopes: MagicMock(name="Credentials"),
    )
    monkeypatch.setattr("app.adapters.sheets.gspread.authorize", lambda creds: MagicMock())

    sheet = GoogleSheet(spreadsheet_id="sheet123", creds_path="creds.json")

    assert sheet._spreadsheet is None  # not connected yet — lazy by design


def test_connect_raises_clear_error_on_bad_creds_path():
    """__init__ never raises (no I/O); the first real connection attempt —
    via _connect(), triggered by any Sheets call — surfaces a clear error
    for a creds file that doesn't exist."""
    sheet = GoogleSheet(spreadsheet_id="sheet123", creds_path="/nonexistent/creds.json")

    with pytest.raises(FileNotFoundError):
        sheet._connect()


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


def test_log_event_prepends_unique_event_id(google_sheet):
    """The real Events tab has a leading event_id column the app has no
    sequence for — each event gets its own uuid so IDs stay unique across
    process restarts, unlike a local counter."""
    sheet, worksheets = google_sheet

    sheet.log_event("lead1", "called", "first")
    sheet.log_event("lead1", "called", "second")
    for _ in range(GoogleSheet.EVENT_BUFFER_MAX - 2):
        sheet.log_event("lead1", "called", "filler")

    flushed = worksheets["Events"].append_rows.call_args[0][0]
    event_ids = [row[0] for row in flushed]
    assert len(set(event_ids)) == len(event_ids)  # all unique
    assert all(event_ids)  # none empty
    assert flushed[0][1:] == ["lead1", flushed[0][2], "called", "first"]


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
