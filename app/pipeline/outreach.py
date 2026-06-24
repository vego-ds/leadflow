"""
Stage 2 - Outreach: fire the call, email and WhatsApp within seconds of arrival.

Each channel — email, WhatsApp, voice call — succeeds or fails independently:
a failure in one is caught, logged as a `<channel>_failed` event, and never
blocks or skips the others. run_outreach's job ends at firing them; it does
not score or book. handle_call_result() picks up from there, but only when
the call itself produced a real outcome — a failed call clears call_result
rather than fabricating one, and the lead falls through to the reconciler /
human-zone path instead of being screened.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.adapters.base import Dialer, Messenger, SheetStore
from app.models import CallResult, Lead, LeadStatus
from app.pipeline.booking import assign_counselor
from app.pipeline.scoring import score_lead

# Default attachments shared with every lead.
DEFAULT_ATTACHMENTS = ["brochure.pdf", "class_schedule.pdf", "why_us.pdf"]


@dataclass
class OutreachResult:
    lead: Lead
    channel_failures: set[str]  # subset of {"email", "whatsapp", "call"}


def run_outreach(
    lead: Lead,
    store: SheetStore,
    dialer: Dialer,
    email: Messenger,
    whatsapp: Messenger,
) -> OutreachResult:
    """Fire email, WhatsApp, and the screening call, each independently.

    A failure in one channel never blocks or skips the others. Returns the
    lead plus which channels (if any) failed; handle_call_result() does the
    scoring and booking that follows, but only when the call succeeded.
    """
    channel_failures: set[str] = set()

    if lead.email:
        try:
            if email.send(lead.email, lead.preferred_language, DEFAULT_ATTACHMENTS):
                store.log_event(lead.lead_id, "email_sent")
        except Exception as exc:  # noqa: BLE001 — one channel's failure must not block the others
            channel_failures.add("email")
            store.log_event(lead.lead_id, "email_failed", str(exc))

    try:
        if whatsapp.send(lead.phone, lead.preferred_language, DEFAULT_ATTACHMENTS):
            store.log_event(lead.lead_id, "whatsapp_sent")
    except Exception as exc:  # noqa: BLE001
        channel_failures.add("whatsapp")
        store.log_event(lead.lead_id, "whatsapp_failed", str(exc))

    try:
        outcome = dialer.call(lead.phone, lead.preferred_language, lead.name)
        lead.call_result = outcome.result
        lead.needs_captured = outcome.needs_captured
    except Exception as exc:  # noqa: BLE001
        channel_failures.add("call")
        store.log_event(lead.lead_id, "call_failed", str(exc))
        # Don't fabricate a screening outcome — leave it unset so the lead
        # falls through to the reconciler / human-zone path instead.
        lead.call_result = None
        lead.needs_captured = ""

    return OutreachResult(lead=lead, channel_failures=channel_failures)


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
