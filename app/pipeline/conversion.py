"""
Stage 6 - Conversion: payment webhook marks the lead Converted.

Idempotent: a payment provider can deliver the same webhook more than once.
We process a given payment_id only the first time using DB-backed deduplication
via store.record_processed_webhook(). A duplicate returns immediately without
mutating the lead, so a lead is never double-converted.
"""
from __future__ import annotations

from app.adapters.base import SheetStore
from app.metrics import conversion_total
from app.models import Lead, LeadStatus


def handle_payment(lead: Lead, payment_id: str, store: SheetStore) -> Lead:
    is_new = store.record_processed_webhook("payment", payment_id)
    if not is_new:
        store.log_event(lead.lead_id, "payment_duplicate_ignored", payment_id)
        return lead

    lead.status = LeadStatus.CONVERTED
    store.update_lead(lead)
    store.log_event(lead.lead_id, "payment_received", payment_id)
    conversion_total.labels(source=lead.source.value, language=lead.preferred_language.value).inc()
    return lead


def mark_lost(lead: Lead, reason: str, store: SheetStore) -> Lead:
    lead.status = LeadStatus.LOST
    store.update_lead(lead)
    store.log_event(lead.lead_id, "marked_lost", reason)
    return lead
