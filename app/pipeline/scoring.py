"""
Stage 3 - Scoring: transparent rule-based priority score (0-100).

Deliberately simple and explainable. The Events log captures the inputs so a
probabilistic/ML model can be trained later on real conversion outcomes.

Signals:
  - source quality   (direct outreach outranks passive form fills)
  - call result      (answered = engaged)
  - data completeness(email present, needs captured)
"""
from __future__ import annotations

from app.adapters.base import SheetStore
from app.models import CallResult, Lead, Source

_SOURCE_WEIGHT = {
    Source.WHATSAPP: 30,    # reached out directly
    Source.INSTAGRAM: 25,
    Source.WEBSITE: 20,
    Source.META_AD: 15,
    Source.MANUAL: 15,
}

_CALL_WEIGHT = {
    CallResult.ANSWERED: 40,
    CallResult.VOICEMAIL: 15,
    CallResult.NO_ANSWER: 5,
}


def score_lead(lead: Lead, store: SheetStore) -> Lead:
    score = _SOURCE_WEIGHT.get(lead.source, 15)
    if lead.call_result:
        score += _CALL_WEIGHT.get(lead.call_result, 0)
    if lead.email:
        score += 10
    if lead.needs_captured:
        score += 20

    lead.score = min(score, 100)
    store.update_lead(lead)
    store.log_event(lead.lead_id, "scored", str(lead.score))
    return lead
