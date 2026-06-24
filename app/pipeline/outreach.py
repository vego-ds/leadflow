"""
Stage 2 - Outreach: fire the call, email and WhatsApp within seconds of arrival.

Each channel — email, WhatsApp, voice call — succeeds or fails independently:
a failure is caught and logged as a `<channel>_failed` event (with the
exception type and message), a success as `<channel>_sent`, and neither
blocks the others. run_outreach's job ends at firing them; it does not score
or book. handle_call_result() picks up from there, but only when the call
itself produced a real outcome — a failed call clears call_result rather
than fabricating one, and the lead falls through to the reconciler /
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


def _is_permanent_error(exc: Exception) -> bool:
    """Classify an exception as permanent (quarantine-worthy) or transient (retry-worthy).

    Permanent: validation, type, auth errors that indicate bad input data.
    Transient: network, timeout, rate limit errors that may succeed on retry.
    Default: transient (safe — reconciler will retry; human will notice if stuck).
    """
    exc_type = type(exc).__name__
    exc_msg = str(exc).lower()

    # Permanent errors — bad data or auth.
    permanent_types = {"ValueError", "ValidationError", "TypeError", "PermissionError"}
    if exc_type in permanent_types:
        return True
    if "403" in exc_msg or "401" in exc_msg or "unauthorized" in exc_msg:
        return True

    # Transient errors — network/infra issues.
    transient_types = {"TimeoutError", "ConnectionError"}
    if exc_type in transient_types:
        return False
    if "429" in exc_msg or "too many requests" in exc_msg.lower():
        return False

    # Default to transient (safe).
    return False


@dataclass
class OutreachResult:
    lead: Lead
    channel_failures: set[str]    # subset of {"email", "whatsapp", "call"}
    channel_successes: set[str]   # subset of {"email", "whatsapp", "call"}
    permanent_error: bool = False  # True if any channel hit a permanent error


def run_outreach(
    lead: Lead,
    store: SheetStore,
    dialer: Dialer,
    email: Messenger,
    whatsapp: Messenger,
) -> OutreachResult:
    """Fire email, WhatsApp, and the screening call, each independently.

    A failure in one channel never blocks or skips the others. Returns the
    lead plus which channels succeeded/failed; handle_call_result() does the
    scoring and booking that follows, but only when the call succeeded. A
    channel that was never attempted (e.g. no email on file) lands in
    neither set — skipped is not the same as failed.
    """
    channel_failures: set[str] = set()
    channel_successes: set[str] = set()

    # Each channel below catches broadly — but never BaseException, so
    # asyncio.CancelledError still propagates — so one channel's failure can
    # never block or skip the others.
    if lead.email:
        try:
            if email.send(lead.email, lead.preferred_language, DEFAULT_ATTACHMENTS):
                channel_successes.add("email")
                store.log_event(lead.lead_id, "email_sent")
        except Exception as exc:  # noqa: BLE001
            channel_failures.add("email")
            store.log_event(lead.lead_id, "email_failed", f"{type(exc).__name__}: {exc}")

    try:
        if whatsapp.send(lead.phone, lead.preferred_language, DEFAULT_ATTACHMENTS):
            channel_successes.add("whatsapp")
            store.log_event(lead.lead_id, "whatsapp_sent")
    except Exception as exc:  # noqa: BLE001
        channel_failures.add("whatsapp")
        store.log_event(lead.lead_id, "whatsapp_failed", f"{type(exc).__name__}: {exc}")

    try:
        outcome = dialer.call(lead.phone, lead.preferred_language, lead.name)
        lead.call_result = outcome.result
        lead.needs_captured = outcome.needs_captured
        channel_successes.add("call")
        store.log_event(lead.lead_id, "call_sent", f"result={outcome.result.value}")
    except Exception as exc:  # noqa: BLE001
        channel_failures.add("call")
        store.log_event(lead.lead_id, "call_failed", f"{type(exc).__name__}: {exc}")
        # Don't fabricate a screening outcome — leave it unset so the lead
        # falls through to the reconciler / human-zone path instead.
        lead.call_result = None
        lead.needs_captured = ""

    return OutreachResult(
        lead=lead, channel_failures=channel_failures, channel_successes=channel_successes
    )


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
