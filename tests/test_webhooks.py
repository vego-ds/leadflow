"""Webhook layer tests. Run: pytest"""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.adapters.sheets import InMemorySheet
from app.models import LeadStatus
from app.pipeline.ingest import validate_and_normalize
from tests.test_pipeline import _good_row


@asynccontextmanager
async def _no_op_lifespan(app):
    """No-op lifespan: skips migrations and reconciler for unit tests."""
    yield


@pytest.fixture()
def test_client():
    import app.main as main_module

    original_store = main_module.store
    original_lifespan = main_module.app.router.lifespan_context

    test_store = InMemorySheet()
    main_module.store = test_store
    main_module.app.router.lifespan_context = _no_op_lifespan

    with TestClient(main_module.app, raise_server_exceptions=True) as client:
        yield client, test_store

    main_module.app.router.lifespan_context = original_lifespan
    main_module.store = original_store


def test_new_lead_responds_before_outreach_runs(test_client):
    client, store = test_client
    r = client.post("/webhook/new-lead", json=_good_row())
    assert r.status_code == 202
    assert r.json()["status"] == "New"


def test_call_result_webhook_rejects_unknown_lead(test_client):
    client, store = test_client
    r = client.post("/webhook/call-result", json={
        "lead_id": "doesnotexist",
        "call_id": "call_001",
        "result": "answered",
    })
    assert r.status_code == 404


def test_call_result_webhook_scores_lead_in_contacted_state(test_client):
    client, store = test_client
    lead = validate_and_normalize(_good_row(), store)
    lead.status = LeadStatus.CONTACTED
    store.update_lead(lead)

    r = client.post("/webhook/call-result", json={
        "lead_id": lead.lead_id,
        "call_id": "call_002",
        "result": "answered",
        "needs_captured": "wants weekend batch",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "Screened"
    assert data["score"] is not None


def test_payment_webhook_converts_known_lead(test_client):
    client, store = test_client
    lead = validate_and_normalize(_good_row(), store)
    r = client.post("/webhook/payment", json={
        "lead_id": lead.lead_id,
        "payment_id": "webhook_pay_1",
    })
    assert r.json() == {
        "accepted": True,
        "lead_id": lead.lead_id,
        "status": "Converted",
    }


def test_payment_webhook_is_idempotent(test_client):
    client, store = test_client
    lead = validate_and_normalize(_good_row(), store)
    client.post("/webhook/payment", json={"lead_id": lead.lead_id, "payment_id": "webhook_pay_2"})
    r = client.post("/webhook/payment", json={"lead_id": lead.lead_id, "payment_id": "webhook_pay_2"})
    assert r.json()["status"] == "Converted"


def test_payment_webhook_rejects_unknown_lead(test_client):
    client, store = test_client
    r = client.post("/webhook/payment", json={"lead_id": "doesnotexist", "payment_id": "webhook_pay_3"})
    assert r.json() == {"accepted": False, "reason": "unknown lead_id"}


def test_health_check(test_client):
    client, store = test_client
    r = client.get("/health")
    assert r.json() == {"status": "ok"}
