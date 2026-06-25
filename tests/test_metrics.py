"""
Metrics tests: foundation (endpoint format, metric objects are functional)
plus pipeline wiring (ingest, outreach, webhooks, reconciler, conversion
actually increment/observe the right metric with the right labels).

Run: pytest -q tests/test_metrics.py
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.adapters.sheets import InMemorySheet
from tests.test_outreach import _FakeDialer, _FakeMessenger, _stub_db_layer


@asynccontextmanager
async def _no_op_lifespan(app):
    """No-op lifespan: skips migrations and reconciler for unit tests."""
    yield


@pytest.fixture()
def client():
    import app.main as main_module
    from app.adapters.sheets import InMemorySheet

    original_store = main_module.store
    original_lifespan = main_module.app.router.lifespan_context
    main_module.store = InMemorySheet()  # never touch the real on-disk DB in tests
    main_module.app.router.lifespan_context = _no_op_lifespan

    with TestClient(main_module.app, raise_server_exceptions=True) as test_client:
        yield test_client

    main_module.app.router.lifespan_context = original_lifespan
    main_module.store = original_store


def _counter_value(counter, **labels) -> float:
    """Read a Counter's current value for a given label set, via the
    public .collect() API rather than the private ._value attribute."""
    for metric in counter.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total") and sample.labels == labels:
                return sample.value
    return 0.0


def _histogram_count(histogram) -> float:
    """Total number of observations recorded on a Histogram."""
    return sum(
        sample.value
        for metric in histogram.collect()
        for sample in metric.samples
        if sample.name.endswith("_count")
    )


def test_metrics_endpoint_returns_prometheus_format(client):
    """GET /metrics returns valid Prometheus text exposing every metric
    defined in app/metrics.py."""
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "leadflow_ingest_total" in body
    assert "leadflow_outreach_attempt_total" in body
    assert "leadflow_webhook_rejection_total" in body
    assert "leadflow_reconciler_retry_total" in body
    assert "leadflow_conversion_total" in body
    assert "leadflow_outreach_latency_seconds" in body
    assert "leadflow_call_duration_seconds" in body
    assert "leadflow_pipeline_state" in body
    assert "leadflow_reconciler_pending" in body


def test_counter_can_increment():
    """Counters are functional — incrementing changes the readable value."""
    from app.metrics import ingest_total

    before = _counter_value(ingest_total, source="web", language="en")
    ingest_total.labels(source="web", language="en").inc()
    after = _counter_value(ingest_total, source="web", language="en")

    assert after == before + 1


def test_counter_without_labels_can_increment():
    from app.metrics import reconciler_retry_total

    before = _counter_value(reconciler_retry_total)
    reconciler_retry_total.inc()
    after = _counter_value(reconciler_retry_total)

    assert after == before + 1


def test_histogram_can_observe():
    """Histograms are functional — observing adds to the bucket counts."""
    from app.metrics import outreach_latency_seconds

    before_count = _histogram_count(outreach_latency_seconds)
    outreach_latency_seconds.observe(2.5)
    after_count = _histogram_count(outreach_latency_seconds)

    assert after_count == before_count + 1


def test_gauge_can_be_set():
    """Gauges are functional — set() updates the readable value."""
    from app.metrics import reconciler_pending_gauge

    reconciler_pending_gauge.set(7)

    for metric in reconciler_pending_gauge.collect():
        for sample in metric.samples:
            if sample.name == "leadflow_reconciler_pending":
                assert sample.value == 7
                return
    pytest.fail("leadflow_reconciler_pending sample not found")


def test_metrics_endpoint_reflects_incremented_counter(client):
    """A metric incremented before the request shows up in /metrics output,
    proving the endpoint reads live state from the shared registry."""
    from app.metrics import webhook_rejection_total

    webhook_rejection_total.labels(type="new_lead", reason="invalid_signature").inc()

    response = client.get("/metrics")

    assert 'leadflow_webhook_rejection_total{reason="invalid_signature",type="new_lead"}' in response.text


# ---------------------------------------------------------------------------
# Pipeline wiring: each metric is actually incremented/observed by real code
# ---------------------------------------------------------------------------

def _make_lead(lead_id: str = "metrics_lead01"):
    from app.models import Language, Lead, LeadStatus, Source

    return Lead(
        lead_id=lead_id,
        name="Metrics Test Lead",
        phone="+919876543210",
        preferred_language=Language.ENGLISH,
        source=Source.WEBSITE,
        email="metrics@example.com",
        status=LeadStatus.NEW,
    )


def test_ingest_increments_counter():
    from app.adapters.sheets import InMemorySheet
    from app.metrics import ingest_total
    from app.pipeline.ingest import validate_and_normalize

    store = InMemorySheet()
    row = {
        "name": "Ingest Metrics Test", "phone": "+919876500001",
        "preferred_language": "en", "source": "website", "email": "i@example.com",
    }

    before = _counter_value(ingest_total, source="website", language="en")
    lead = validate_and_normalize(row, store)
    after = _counter_value(ingest_total, source="website", language="en")

    assert lead is not None
    assert after == before + 1


def test_outreach_attempt_success_increments_with_outcome_label(_stub_db_layer):
    from app.metrics import outreach_attempt_total
    from app.pipeline.outreach_durable import run_outreach_durable

    store = InMemorySheet()
    store.seed_counselors([{
        "name": "Anita", "languages_spoken": ["en"],
        "available_slots": ["Mon 10"], "current_load": 0,
    }])
    lead = _make_lead("metrics_outreach_success")
    store.append_lead(lead)

    before = _counter_value(outreach_attempt_total, outcome="success")
    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(), email=_FakeMessenger(), whatsapp=_FakeMessenger(),
    ))
    after = _counter_value(outreach_attempt_total, outcome="success")

    assert after == before + 1


def test_outreach_attempt_permanent_error_increments_with_outcome_label(_stub_db_layer):
    from app.metrics import outreach_attempt_total
    from app.pipeline.outreach_durable import run_outreach_durable

    store = InMemorySheet()
    store.seed_counselors([{
        "name": "Anita", "languages_spoken": ["en"],
        "available_slots": ["Mon 10"], "current_load": 0,
    }])
    lead = _make_lead("metrics_outreach_permanent")
    store.append_lead(lead)

    before = _counter_value(outreach_attempt_total, outcome="permanent_error")
    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(),
        email=_FakeMessenger(error=ValueError("invalid address")), whatsapp=_FakeMessenger(),
    ))
    after = _counter_value(outreach_attempt_total, outcome="permanent_error")

    assert after == before + 1


def test_outreach_latency_is_observed(_stub_db_layer):
    from app.metrics import outreach_latency_seconds
    from app.pipeline.outreach_durable import run_outreach_durable

    store = InMemorySheet()
    store.seed_counselors([{
        "name": "Anita", "languages_spoken": ["en"],
        "available_slots": ["Mon 10"], "current_load": 0,
    }])
    lead = _make_lead("metrics_outreach_latency")
    store.append_lead(lead)

    before_count = _histogram_count(outreach_latency_seconds)
    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(), email=_FakeMessenger(), whatsapp=_FakeMessenger(),
    ))
    after_count = _histogram_count(outreach_latency_seconds)

    assert after_count == before_count + 1


def test_webhook_rejection_increments_when_lead_is_quarantined(client):
    """A POST that fails LeadFlow's own business validation (not Pydantic's
    type check) gets quarantined and counted as a rejection."""
    from app.metrics import webhook_rejection_total

    before = _counter_value(webhook_rejection_total, type="new_lead", reason="missing_field")
    response = client.post("/webhook/new-lead", json={
        "name": "", "phone": "123", "preferred_language": "xx", "source": "website",
    })
    after = _counter_value(webhook_rejection_total, type="new_lead", reason="missing_field")

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert after == before + 1


def test_reconciler_retry_increments_on_stale_lead(tmp_path):
    from app.metrics import reconciler_retry_total
    from tests.test_durability import _make_lead as _durable_make_lead
    from tests.test_durability import _setup_temp_db

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'metrics_reconciler.db'}"

    async def _run() -> tuple[float, float]:
        engine = await _setup_temp_db(db_url)
        try:
            from app.adapters.db_store import DbStore
            from app.db.engine import async_session
            from app.db.models import LeadRow
            from sqlalchemy import update

            store = DbStore()
            lead = _durable_make_lead("metrics_stale01")
            store.append_lead(lead)

            stale_time = datetime.now(timezone.utc) - timedelta(minutes=5)
            async with async_session() as session:
                await session.execute(
                    update(LeadRow)
                    .where(LeadRow.lead_id == "metrics_stale01")
                    .values(outreach_state="IN_PROGRESS", outreach_started_at=stale_time, outreach_attempts=1)
                )
                await session.commit()

            import app.pipeline.outreach_durable as od_mod

            async def _mock_durable(lead_id, **kwargs):
                pass

            original = od_mod.run_outreach_durable
            od_mod.run_outreach_durable = _mock_durable
            try:
                from app.reconciler import _reconcile_once

                before = _counter_value(reconciler_retry_total)
                await _reconcile_once()
                await asyncio.sleep(0.05)  # let create_task coroutines run
                after = _counter_value(reconciler_retry_total)
            finally:
                od_mod.run_outreach_durable = original
            return before, after
        finally:
            await engine.dispose()

    before, after = asyncio.run(_run())
    assert after == before + 1


def test_conversion_increments_counter():
    from app.adapters.sheets import InMemorySheet
    from app.metrics import conversion_total
    from app.pipeline.conversion import handle_payment

    store = InMemorySheet()
    lead = _make_lead("metrics_conversion01")
    store.append_lead(lead)

    before = _counter_value(conversion_total, source=lead.source.value, language=lead.preferred_language.value)
    handle_payment(lead, "pay_metrics_001", store)
    after = _counter_value(conversion_total, source=lead.source.value, language=lead.preferred_language.value)

    assert after == before + 1
