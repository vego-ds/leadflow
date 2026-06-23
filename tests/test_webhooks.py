"""Webhook layer tests. Run: pytest"""
from app.main import NewLead, PaymentEvent, new_lead, payment
from tests.test_pipeline import _good_row


def _good_lead():
    return NewLead(**_good_row())


def test_payment_webhook_converts_known_lead():
    created = new_lead(_good_lead())
    result = payment(PaymentEvent(lead_id=created["lead_id"], payment_id="webhook_pay_1"))
    assert result == {"accepted": True, "lead_id": created["lead_id"], "status": "Converted"}


def test_payment_webhook_is_idempotent():
    created = new_lead(_good_lead())
    payment(PaymentEvent(lead_id=created["lead_id"], payment_id="webhook_pay_2"))
    result = payment(PaymentEvent(lead_id=created["lead_id"], payment_id="webhook_pay_2"))
    assert result["status"] == "Converted"


def test_payment_webhook_rejects_unknown_lead():
    result = payment(PaymentEvent(lead_id="doesnotexist", payment_id="webhook_pay_3"))
    assert result == {"accepted": False, "reason": "unknown lead_id"}
