"""
Integration tests for the GoogleSheet adapter and the store factory — prove
the mock -> live swap works end-to-end against the real spreadsheet
configured in .env.

Part 1 tests make real network calls and are opt-in only (see pytest.ini's
`addopts`, which excludes the `integration` marker from the default run —
otherwise every bare `pytest -q` would hit the live Google Sheets API).
Each also skips cleanly if GOOGLE_CREDS_PATH/SPREADSHEET_ID aren't
configured. Part 2 tests the existing `app.main._build_store()` factory
purely via config/object state — no network calls, so they run unmarked
as part of the normal suite.

Run:  pytest -q -m integration tests/test_integration_sheets.py
"""
from __future__ import annotations

import uuid

import pytest

from app.adapters.sheets import GoogleSheet
from app.config import settings
from app.models import Language, Lead, LeadStatus, Source
from app.pipeline.ingest import validate_and_normalize

_live_creds_configured = bool(settings.spreadsheet_id and settings.google_creds_path)
requires_live_sheets = pytest.mark.skipif(
    not _live_creds_configured,
    reason="GOOGLE_CREDS_PATH/SPREADSHEET_ID not configured in .env",
)


def _unique_lead_id() -> str:
    return f"integration_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def real_sheets_adapter():
    """Real GoogleSheet adapter pointing at the spreadsheet configured in
    .env. Yields (adapter, touched_lead_ids) — append the lead_id of
    anything you write to `touched_lead_ids` and teardown deletes every row
    containing it from Leads, Needs Review, and Events. GoogleSheet has no
    delete_lead method (none was added; out of this task's scope), so
    cleanup is manual, direct worksheet access."""
    adapter = GoogleSheet(spreadsheet_id=settings.spreadsheet_id, creds_path=settings.google_creds_path)
    touched_lead_ids: list[str] = []
    yield adapter, touched_lead_ids
    for lead_id in touched_lead_ids:
        _delete_lead_id_everywhere(adapter, lead_id)


def _delete_lead_id_everywhere(adapter: GoogleSheet, lead_id: str) -> None:
    for tab_name in (adapter.LEADS_TAB, adapter.NEEDS_REVIEW_TAB, adapter.EVENTS_TAB):
        worksheet = adapter._worksheet(tab_name)
        rows = worksheet.get_all_values()
        # Bottom-up so deleting a row doesn't shift the index of rows still
        # to be checked. Row 1 is the header — never touch it.
        for i in range(len(rows), 1, -1):
            if lead_id in rows[i - 1]:
                worksheet.delete_rows(i)
    adapter._lead_row_cache.pop(lead_id, None)


# ---------------------------------------------------------------------------
# Part 1: real adapter smoke tests — opt-in, real network calls
# ---------------------------------------------------------------------------

@pytest.mark.integration
@requires_live_sheets
def test_real_sheets_append_and_retrieve_lead(real_sheets_adapter):
    adapter, touched = real_sheets_adapter
    lead_id = _unique_lead_id()
    touched.append(lead_id)

    lead = Lead(
        lead_id=lead_id,
        name="Integration Test Lead",
        phone="+910000000001",
        preferred_language=Language.ENGLISH,
        source=Source.MANUAL,
        email="integration@example.com",
        status=LeadStatus.NEW,
    )

    adapter.append_lead(lead)
    retrieved = adapter.get_lead(lead_id)

    assert retrieved is not None
    assert retrieved.lead_id == lead.lead_id
    assert retrieved.name == lead.name
    assert retrieved.phone == lead.phone
    assert retrieved.email == lead.email
    assert retrieved.preferred_language == lead.preferred_language
    assert retrieved.source == lead.source
    assert retrieved.status == lead.status


@pytest.mark.integration
@requires_live_sheets
def test_real_sheets_get_counselors(real_sheets_adapter):
    """get_counselors() returns the contract GoogleSheet actually
    implements: name, languages_spoken, available_slots, current_load.
    No counselor_id — the real sheet has that column, but the app has no
    counselor_id concept (counselors are identified by name)."""
    adapter, _ = real_sheets_adapter

    counselors = adapter.get_counselors()

    assert isinstance(counselors, list)
    for counselor in counselors:
        assert set(counselor.keys()) == {
            "name", "languages_spoken", "available_slots", "current_load",
        }
        assert isinstance(counselor["name"], str)
        assert isinstance(counselor["languages_spoken"], list)
        assert isinstance(counselor["available_slots"], list)
        assert isinstance(counselor["current_load"], int)


@pytest.mark.integration
@requires_live_sheets
def test_real_sheets_log_event_flushes_to_sheets(real_sheets_adapter):
    adapter, touched = real_sheets_adapter
    lead_id = _unique_lead_id()
    touched.append(lead_id)

    # EVENT_BUFFER_MAX entries trigger a size-based flush immediately —
    # no sleep needed (test-discipline: no time.sleep in tests).
    for i in range(GoogleSheet.EVENT_BUFFER_MAX):
        adapter.log_event(lead_id, "integration_test_event", f"detail-{i}")

    events_ws = adapter._worksheet(adapter.EVENTS_TAB)
    rows = events_ws.get_all_values()
    matching = [r for r in rows if lead_id in r]
    assert len(matching) == GoogleSheet.EVENT_BUFFER_MAX


# ---------------------------------------------------------------------------
# Part 2: factory wiring — app.main._build_store(), no network calls
# ---------------------------------------------------------------------------

def test_build_store_wraps_google_sheet_when_configured(monkeypatch):
    """When GOOGLE_CREDS_PATH and SPREADSHEET_ID are both set,
    _build_store() returns a ProjectionStore wrapping a GoogleSheet."""
    import app.main as main_module

    monkeypatch.setattr(main_module.settings, "spreadsheet_id", "fake-sheet-id")
    monkeypatch.setattr(main_module.settings, "google_creds_path", "fake-creds.json")

    store = main_module._build_store()

    assert isinstance(store._sheet, GoogleSheet)


def test_build_store_is_db_only_when_not_configured(monkeypatch):
    """When either GOOGLE_CREDS_PATH or SPREADSHEET_ID is missing,
    _build_store() returns a ProjectionStore with no Sheets mirror."""
    import app.main as main_module

    monkeypatch.setattr(main_module.settings, "spreadsheet_id", None)
    monkeypatch.setattr(main_module.settings, "google_creds_path", None)

    store = main_module._build_store()

    assert store._sheet is None


# ---------------------------------------------------------------------------
# Part 3: end-to-end — a real pipeline stage (ingest) writes to the real Sheet
# ---------------------------------------------------------------------------

@pytest.mark.integration
@requires_live_sheets
def test_end_to_end_lead_reaches_real_sheets(real_sheets_adapter):
    """A lead run through the real ingest stage (validate_and_normalize)
    lands in the real Sheet's Leads tab — proves the mock -> live swap
    end-to-end, not just direct adapter calls."""
    adapter, touched = real_sheets_adapter

    row = {
        "name": "Integration E2E Lead",
        "phone": "+910000000002",
        "preferred_language": "en",
        "source": "website",
        "email": "e2e@example.com",
        "raw_notes": "Integration test",
    }

    lead = validate_and_normalize(row, adapter)
    assert lead is not None, "lead was quarantined instead of validated"
    touched.append(lead.lead_id)

    retrieved = adapter.get_lead(lead.lead_id)
    assert retrieved is not None
    assert retrieved.name == "Integration E2E Lead"
    assert retrieved.phone == "+910000000002"
    assert retrieved.email == "e2e@example.com"
    assert retrieved.preferred_language == Language.ENGLISH
    assert retrieved.source == Source.WEBSITE
