"""Webhook layer tests. Run: pytest"""
import pytest
from fastapi import BackgroundTasks, HTTPException, Response

from app.main import CallResultEvent, NewLead, PaymentEvent, call_result, new_lead, payment, store
from tests.test_pipeline import _good_row


def _good_lead():
    return NewLead(**_good_row())


def _create_lead(run_background: bool = False) -> dict:
    """Calls new_lead() directly, then - unless told not to - runs its
    enqueued background task synchronously, the way FastAPI would after
    sending the response."""
    background_tasks = BackgroundTasks()
    created = new_lead(_good_lead(), background_tasks, Response())
    if run_background:
        for task in background_tasks.tasks:
            task.func(*task.args, **task.kwargs)
    return created


def test_new_lead_responds_before_outreach_runs():
    created = _create_lead()
    assert created["status"] == "New"


def test_background_outreach_screens_the_lead():
    created = _create_lead(run_background=True)
    lead = store.get_lead(created["lead_id"])
    assert lead.call_result is not None
    assert lead.status.value == "Screened"


def test_call_result_webhook_scores_and_attempts_assignment():
    created = _create_lead()
    result = call_result(CallResultEvent(
        lead_id=created["lead_id"], result="answered", needs_captured="wants weekend batch"
    ))
    assert result["status"] == "Screened"
    assert result["score"] is not None
    assert result["assigned_counselor"] is None  # no counselors seeded in the demo server


def test_call_result_webhook_rejects_unknown_lead():
    with pytest.raises(HTTPException) as exc_info:
        call_result(CallResultEvent(lead_id="doesnotexist", result="answered"))
    assert exc_info.value.status_code == 404


def test_payment_webhook_converts_known_lead():
    created = _create_lead()
    result = payment(PaymentEvent(lead_id=created["lead_id"], payment_id="webhook_pay_1"))
    assert result == {"accepted": True, "lead_id": created["lead_id"], "status": "Converted"}


def test_payment_webhook_is_idempotent():
    created = _create_lead()
    payment(PaymentEvent(lead_id=created["lead_id"], payment_id="webhook_pay_2"))
    result = payment(PaymentEvent(lead_id=created["lead_id"], payment_id="webhook_pay_2"))
    assert result["status"] == "Converted"


def test_payment_webhook_rejects_unknown_lead():
    result = payment(PaymentEvent(lead_id="doesnotexist", payment_id="webhook_pay_3"))
    assert result == {"accepted": False, "reason": "unknown lead_id"}
