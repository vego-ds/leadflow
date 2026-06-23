"""
Stage 2 - Outreach: fire the call, email and WhatsApp within seconds of arrival.

Email and WhatsApp go out regardless of whether the call is answered.
The screening result from the call is captured here and carried forward.
"""
from __future__ import annotations

from app.adapters.base import Dialer, Messenger
from app.adapters.base import SheetStore
from app.models import Lead, LeadStatus

# Default attachments shared with every lead.
DEFAULT_ATTACHMENTS = ["brochure.pdf", "class_schedule.pdf", "why_us.pdf"]


def run_outreach(
    lead: Lead,
    store: SheetStore,
    dialer: Dialer,
    email: Messenger,
    whatsapp: Messenger,
) -> Lead:
    # Email + WhatsApp: fire-and-forget, independent of call pickup.
    if lead.email:
        if email.send(lead.email, lead.preferred_language, DEFAULT_ATTACHMENTS):
            store.log_event(lead.lead_id, "email_sent")
    if whatsapp.send(lead.phone, lead.preferred_language, DEFAULT_ATTACHMENTS):
        store.log_event(lead.lead_id, "whatsapp_sent")

    # Voice call: screening.
    outcome = dialer.call(lead.phone, lead.preferred_language, lead.name)
    lead.call_result = outcome.result
    lead.needs_captured = outcome.needs_captured
    store.log_event(lead.lead_id, "call_result", outcome.result.value)

    lead.status = LeadStatus.CONTACTED
    store.update_lead(lead)
    return lead
