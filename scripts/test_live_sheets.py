"""
Live GoogleSheet adapter smoke test — opt-in, real network calls, real
credentials. Not part of `pytest` (see adapter-discipline: real provider
calls live outside tests/, never run automatically).

Appends a synthetic lead, reads it back, asserts round-trip equality, then
deletes the row so no test data is left behind in the real spreadsheet.

Requires GOOGLE_CREDS_PATH and SPREADSHEET_ID set in .env.

Run:  python -m scripts.test_live_sheets
"""
from __future__ import annotations

import sys

from app.adapters.sheets import GoogleSheet
from app.config import settings
from app.models import Language, Lead, LeadStatus, Source
from app.utils.logging import log

LEAD_ID = "live_smoke_test_001"


def main() -> None:
    if not (settings.spreadsheet_id and settings.google_creds_path):
        log("GOOGLE_CREDS_PATH and SPREADSHEET_ID must both be set in .env to run this.")
        sys.exit(1)

    sheet = GoogleSheet(spreadsheet_id=settings.spreadsheet_id, creds_path=settings.google_creds_path)

    lead = Lead(
        lead_id=LEAD_ID,
        name="Live Smoke Test",
        phone="+910000000000",
        preferred_language=Language.ENGLISH,
        source=Source.MANUAL,
        email="livesmoketest@example.com",
        status=LeadStatus.NEW,
    )

    log(f"Appending lead {LEAD_ID}...")
    sheet.append_lead(lead)

    log("Reading it back...")
    fetched = sheet.get_lead(LEAD_ID)
    assert fetched is not None, "round-trip failed: lead not found after append"
    assert fetched.lead_id == lead.lead_id
    assert fetched.name == lead.name
    assert fetched.phone == lead.phone
    assert fetched.email == lead.email
    assert fetched.preferred_language == lead.preferred_language
    assert fetched.source == lead.source
    assert fetched.status == lead.status
    log(f"Round-trip OK: {fetched}")

    log("Cleaning up test row...")
    row_index = sheet._find_lead_row(LEAD_ID)
    sheet._worksheet(sheet.LEADS_TAB).delete_rows(row_index)
    log("Done — live adapter round-trip verified, no test data left behind.")


if __name__ == "__main__":
    main()
