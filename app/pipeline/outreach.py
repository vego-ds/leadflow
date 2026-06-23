"""
Stage 2 - Outreach: fire the call, email and WhatsApp within seconds of arrival.

Email and WhatsApp go out regardless of whether the call is answered.
run_outreach's job ends at firing them and producing a call outcome - it does
not score or book. handle_call_result() picks up from there and is shared by
two callers: the background task that runs after MockDialer's call returns
synchronously, and POST /webhook/call-result, which is where a real Bolna
callback lands once an actual call completes asynchronously.
"""
from __future__ import annotations

from app.adapters.base import CallOutcome, Dialer, Messenger, SheetStore
from app.models import CallResult, Lead, LeadStatus
from app.pipeline.booking import assign_counselor
from app.pipeline.scoring import score_lead

# Default attachments shared with every lead.
DEFAULT_ATTACHMENTS = ["brochure.pdf", "class_schedule.pdf", "why_us.pdf"]


def run_outreach(
    lead: Lead,
    store: SheetStore,
    dialer: Dialer,
    email: Messenger,
    whatsapp: Messenger,
) -> CallOutcome:
    """Fire email, WhatsApp, and the screening call. Returns the call outcome;
    handle_call_result() does the scoring and booking that follows."""
    # Email + WhatsApp: fire-and-forget, independent of call pickup.
    if lead.email:
        if email.send(lead.email, lead.preferred_language, DEFAULT_ATTACHMENTS):
            store.log_event(lead.lead_id, "email_sent")
    if whatsapp.send(lead.phone, lead.preferred_language, DEFAULT_ATTACHMENTS):
        store.log_event(lead.lead_id, "whatsapp_sent")

    return dialer.call(lead.phone, lead.preferred_language, lead.name)


def handle_call_result(
    lead: Lead, result: CallResult, needs_captured: str, store: SheetStore
) -> Lead:
    """Record a screening outcome, then score and attempt counselor assignment."""
    lead.call_result = result
    lead.needs_captured = needs_captured
    lead.status = LeadStatus.SCREENED
    store.update_lead(lead)
    store.log_event(lead.lead_id, "call_result", result.value)

    score_lead(lead, store)
    assign_counselor(lead, store)
    return lead
