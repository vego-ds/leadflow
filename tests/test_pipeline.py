"""Basic pipeline tests. Run: pytest"""
from app.adapters.sheets import InMemorySheet
from app.models import CallResult, Language, Source
from app.pipeline.conversion import handle_payment
from app.pipeline.ingest import validate_and_normalize
from app.pipeline.scoring import score_lead


def _good_row():
    return {"name": "Test User", "phone": "+919876543210", "email": "t@example.com",
            "source": "website", "preferred_language": "hi", "raw_notes": "x"}


def test_valid_lead_is_accepted():
    store = InMemorySheet()
    lead = validate_and_normalize(_good_row(), store)
    assert lead is not None
    assert lead.preferred_language == Language.HINDI
    assert len(store.leads) == 1


def test_bad_phone_is_quarantined():
    store = InMemorySheet()
    row = _good_row() | {"phone": "abcd"}
    assert validate_and_normalize(row, store) is None
    assert len(store.needs_review) == 1


def test_unknown_language_is_quarantined():
    store = InMemorySheet()
    row = _good_row() | {"preferred_language": "zz"}
    assert validate_and_normalize(row, store) is None


def test_answered_call_scores_higher():
    store = InMemorySheet()
    lead = validate_and_normalize(_good_row(), store)
    lead.call_result = CallResult.ANSWERED
    lead.needs_captured = "wants weekend batch"
    score_lead(lead, store)
    assert lead.score >= 70


def test_payment_is_idempotent():
    store = InMemorySheet()
    lead = validate_and_normalize(_good_row(), store)
    handle_payment(lead, "pay_1", store)
    handle_payment(lead, "pay_1", store)  # duplicate
    received = [e for e in store.events if e["event_type"] == "payment_received"]
    assert len(received) == 1
