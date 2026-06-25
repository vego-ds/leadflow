"""
Metrics foundation tests — definitions only. Confirms the /metrics endpoint
serves valid Prometheus text and that the metric objects are functional
(can be incremented/observed and read back). Wiring these into the
pipeline (actually calling .inc()/.observe() during ingest, outreach,
webhooks, etc.) is a separate, later task.

Run: pytest -q tests/test_metrics.py
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient


@asynccontextmanager
async def _no_op_lifespan(app):
    """No-op lifespan: skips migrations and reconciler for unit tests."""
    yield


@pytest.fixture()
def client():
    import app.main as main_module

    original_lifespan = main_module.app.router.lifespan_context
    main_module.app.router.lifespan_context = _no_op_lifespan

    with TestClient(main_module.app, raise_server_exceptions=True) as test_client:
        yield test_client

    main_module.app.router.lifespan_context = original_lifespan


def _counter_value(counter, **labels) -> float:
    """Read a Counter's current value for a given label set, via the
    public .collect() API rather than the private ._value attribute."""
    for metric in counter.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total") and sample.labels == labels:
                return sample.value
    return 0.0


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

    before_count = sum(
        sample.value
        for metric in outreach_latency_seconds.collect()
        for sample in metric.samples
        if sample.name.endswith("_count")
    )

    outreach_latency_seconds.observe(2.5)

    after_count = sum(
        sample.value
        for metric in outreach_latency_seconds.collect()
        for sample in metric.samples
        if sample.name.endswith("_count")
    )

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
