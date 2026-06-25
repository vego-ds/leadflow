"""
Prometheus metrics — definitions only.

Metrics live on their own CollectorRegistry (not the prometheus_client
global default) so /metrics output is exactly what this module defines,
nothing picked up incidentally from another library. Exposed via the
/metrics endpoint in app/main.py.

Not wired into pipeline code yet — these are declared but nothing
increments/observes them outside tests. That's the next task.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

ingest_total = Counter(
    "leadflow_ingest_total",
    "Total leads ingested",
    ["source", "language"],
    registry=REGISTRY,
)

outreach_attempt_total = Counter(
    "leadflow_outreach_attempt_total",
    "Total outreach attempts",
    ["outcome"],  # outcome: success, permanent_error, transient_error
    registry=REGISTRY,
)

webhook_rejection_total = Counter(
    "leadflow_webhook_rejection_total",
    "Total rejected webhooks",
    ["type", "reason"],  # type: new_lead, call_result; reason: invalid_signature, missing_field, etc.
    registry=REGISTRY,
)

reconciler_retry_total = Counter(
    "leadflow_reconciler_retry_total",
    "Total retries triggered by reconciler",
    registry=REGISTRY,
)

conversion_total = Counter(
    "leadflow_conversion_total",
    "Total converted leads",
    ["source", "language"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------

outreach_latency_seconds = Histogram(
    "leadflow_outreach_latency_seconds",
    "Time from ingest to first outreach attempt",
    buckets=[1, 5, 10, 30, 60, 300, 600, 1800],  # 1s, 5s, 10s, 30s, 1m, 5m, 10m, 30m
    registry=REGISTRY,
)

call_duration_seconds = Histogram(
    "leadflow_call_duration_seconds",
    "Duration of screening calls",
    buckets=[10, 30, 60, 120, 300, 600],  # 10s to 10m
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------

pipeline_state_gauge = Gauge(
    "leadflow_pipeline_state",
    "Leads by status",
    ["status"],  # NEW, CONTACTED, SCREENED, ASSIGNED, IN_DISCUSSION, PAYMENT_LINK_SENT, CONVERTED, LOST, QUARANTINED
    registry=REGISTRY,
)

reconciler_pending_gauge = Gauge(
    "leadflow_reconciler_pending",
    "Leads in PENDING/IN_PROGRESS awaiting reconciler sweep",
    registry=REGISTRY,
)
