"""
Stage 4 - Booking: auto-assign the next available counselor.

Prefers a counselor who speaks the lead's language and has the lightest load.
Falls back to any available counselor if no language match exists.
"""
from __future__ import annotations

from app.adapters.base import SheetStore
from app.models import Lead, LeadStatus


def assign_counselor(lead: Lead, counselors: list[dict], store: SheetStore) -> Lead:
    lang = lead.preferred_language.value

    candidates = [
        c for c in counselors
        if lang in c.get("languages_spoken", []) and c.get("available_slots")
    ]
    if not candidates:
        candidates = [c for c in counselors if c.get("available_slots")]

    if not candidates:
        # No availability; leave for human handling, don't lose the lead.
        store.log_event(lead.lead_id, "assignment_failed", "no counselor available")
        return lead

    chosen = min(candidates, key=lambda c: c.get("current_load", 0))
    slot = chosen["available_slots"].pop(0)
    chosen["current_load"] = chosen.get("current_load", 0) + 1

    lead.assigned_counselor = chosen["name"]
    lead.counselor_slot = slot
    lead.status = LeadStatus.ASSIGNED
    store.update_lead(lead)
    store.log_event(
        lead.lead_id, "counselor_assigned", f"{chosen['name']} @ {slot}"
    )
    return lead
