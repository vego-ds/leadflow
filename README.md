# LeadFlow — Multilingual Voice-First Lead Conversion Pipeline

Closes the gap between **lead intent and lead response** — instantly, consistently, and in the lead's own language — while keeping humans in control of judgment-heavy decisions (screening review, counselor calls, closing).

Built around a real EdTech problem: leads arrive from many channels (Meta ads, website, WhatsApp, Instagram) and lose warmth with every minute they wait. LeadFlow responds within seconds, in the lead's language, and tracks each lead all the way to payment.

> **Note:** External services (voice, email, WhatsApp, Sheets, payment) are **mocked** in this repo so it runs with zero credentials and zero cost. Each mock sits behind the same interface as its real counterpart — going live is a swap, not a rewrite.

---

## What it does

```
Lead arrives (Meta ad / website / WhatsApp / Instagram)
   │
   ├─ within seconds ─ voice call (multilingual screening, Bolna)
   ├─ email   + attachments  (regardless of call pickup)
   └─ whatsapp + attachments
   │
Screening outcome captured ─► Scored ─► Counselor auto-assigned
   │
── human zone (tracked) ── counselor works lead ─► payment link
   │
Payment webhook ─► Converted   (Lost branch available at any stage)
```

**Status lifecycle:** `New → Contacted → Screened → Assigned → InDiscussion → PaymentLinkSent → Converted` (+ `Lost`)

## Architecture

| Concern | Choice | Why |
|---|---|---|
| **System of record** | **SQLite (SQLAlchemy async)** | **Lead state, idempotency, and outreach durability persist across restarts** |
| Human-facing view | Google Sheets (4 tabs) | The team already lives in Sheets; no new tool to adopt. Sheets are a *projection*, not the source of truth |
| Real-time ingestion | Apps Script trigger → HMAC-signed webhook | Instant reaction without a message queue; signatures prevent spoofing |
| Backend | Python + FastAPI | Light, readable, easy to extend |
| Voice | Bolna AI (multilingual) | Native Indian-language voice agents; mocked here |
| Reliability | DB-backed idempotency + reconciler loop | Durable across restarts; stale outreach tasks auto-recovered every 30s |
| Webhook security | HMAC-SHA256 + 5-min replay window | Prevents spoofed and replayed webhook deliveries |

**Sheet tabs:** `Leads` · `Needs Review` (quarantined bad rows) · `Counselors` (auto-assignment) · `Events` (activity log for scoring).

Design principle: **start with the simplest thing that works; add infrastructure only when a concrete problem demands it.** The DB is SQLite with Alembic migrations — no Postgres-specific features, so migration to a hosted DB is a connection-string swap.

## Languages

English, Hindi, Telugu, Kannada, Tamil, Malayalam — selected per lead's stated preference, with auto-switch as fallback. See [`bolna_agent/agent_config.json`](bolna_agent/agent_config.json) for the per-language agent config.

## Run the demo

```bash
pip install -r requirements.txt
python -m scripts.run_demo
```

Runs 10 synthetic leads (including malformed rows) end-to-end through the pipeline and prints a status summary. No credentials needed.

```bash
# API mode
uvicorn app.main:app --reload
# POST a lead to http://localhost:8000/webhook/new-lead
```

## Tests

```bash
pytest
```

Covers validation, quarantine, scoring, and payment idempotency.

## Going live (the swap)

| Demo | Live |
|---|---|
| `InMemorySheet` | `GoogleSheet` (gspread) |
| `MockDialer` | `BolnaDialer` (Bolna API) |
| `MockEmail` / `MockWhatsApp` | SMTP / WhatsApp Business API |

Interfaces stay identical — only the adapter and credentials change.

## Project layout

```
app/
  models.py            # Lead schema + status lifecycle
  main.py              # FastAPI webhooks
  pipeline/            # ingest · outreach · scoring · booking · conversion
  adapters/            # mock + real-stub integrations (one interface each)
bolna_agent/           # multilingual voice agent config
apps_script/           # Google Sheets real-time trigger
scripts/run_demo.py    # end-to-end demo runner
data/                  # synthetic sample leads
tests/                 # validation, scoring, idempotency
```

## Roadmap

**Done:**
- Per-channel outreach resilience ✓ — email/WhatsApp/call each succeed or fail independently (`3b37c62`, `6cb52cb`)
- Step-level outreach logging ✓ — full attempt audit trail in the Events tab (`0cc198c`)
- Smart quarantine ✓ — permanent errors → Needs Review, transient errors → reconciler retry (`a5d58bf`, `ede2a40`, `b2886d8`, `8a7d872`)
- Google Sheets live adapter ✓ — real `gspread` integration + opt-in integration tests (`a632208`, `6ba8147`, `1f4e06c`, `3123e42`)
- Prometheus metrics foundation ✓ — `/metrics` endpoint, 9 metrics defined (`c9832b8`); wiring into pipeline code is next

**In progress:**
- Wire metrics into pipeline — increment/observe during ingest, outreach, webhooks, conversion

**Deferred:**
- ML-based lead scoring (features already logged to the event store)
- Post-payment automation: invoice generation, onboarding email + credentials
- Dashboard — UI showing pipeline health by status/source/language
- Structured logging (JSON format + correlation IDs)
- Postgres migration when SQLite WAL mode hits throughput limits (connection-string swap, no SQL changes needed)
- Real task queue (ARQ or similar) if BackgroundTasks + reconciler proves insufficient at scale
