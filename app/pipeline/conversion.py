"""
Stage 6 - Conversion: payment webhook marks the lead Converted.

Idempotent: a payment provider can deliver the same webhook more than once.
We process a given payment_id only the first time and ignore duplicates, so a
lead is never double-converted.
"""
from __future__ import annotations

from app.adapters.base import SheetStore
from app.models import Lead, LeadStatus

_processed_payments: set[str] = set()


def handle_payment(lead: Lead, payment_id: str, store: SheetStore) -> Lead:
    if payment_id in _processed_payments:
        store.log_event(lead.lead_id, "payment_duplicate_ignored", payment_id)
        return lead

    _processed_payments.add(payment_id)
    lead.status = LeadStatus.CONVERTED
    store.update_lead(lead)
    store.log_event(lead.lead_id, "payment_received", payment_id)
    return lead


def mark_lost(lead: Lead, reason: str, store: SheetStore) -> Lead:
    lead.status = LeadStatus.LOST
    store.update_lead(lead)
    store.log_event(lead.lead_id, "marked_lost", reason)
    return lead
