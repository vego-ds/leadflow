# Prometheus Metrics

## Overview

LeadFlow exposes pipeline health and performance metrics via the `/metrics` endpoint in Prometheus text format. Definitions live in `app/metrics.py`.

## Metrics (9 total)

### Counters (cumulative)

- `leadflow_ingest_total` — Total leads ingested (labels: `source`, `language`)
- `leadflow_outreach_attempt_total` — Total outreach attempts (label: `outcome`=[success|permanent_error|transient_error])
- `leadflow_webhook_rejection_total` — Total rejected webhooks (labels: `type`=[new_lead|call_result], `reason`)
- `leadflow_reconciler_retry_total` — Total retries triggered by reconciler
- `leadflow_conversion_total` — Total leads converted to payment (labels: `source`, `language`)

### Histograms (latency/duration)

- `leadflow_outreach_latency_seconds` — Time from ingest to first outreach attempt (buckets: 1s, 5s, 10s, 30s, 1m, 5m, 10m, 30m)
- `leadflow_call_duration_seconds` — Duration of screening calls (buckets: 10s, 30s, 1m, 2m, 5m, 10m)

### Gauges (current state)

- `leadflow_pipeline_state` — Leads by status (label: `status`=[NEW|CONTACTED|SCREENED|ASSIGNED|IN_DISCUSSION|PAYMENT_LINK_SENT|CONVERTED|LOST|QUARANTINED])
- `leadflow_reconciler_pending` — Count of leads in PENDING/IN_PROGRESS awaiting reconciler sweep

## Access

```bash
curl http://localhost:8000/metrics
```

Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: leadflow
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
```

## Status

**Foundation only** — Metrics are defined and the `/metrics` endpoint is live (`c9832b8`). Nothing in the pipeline increments or observes them yet. Wiring them in (incrementing counters during ingest, observing latencies in outreach, etc.) is the next task.
