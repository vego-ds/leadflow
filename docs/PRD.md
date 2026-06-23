# LeadFlow — Product Requirements Document

**Status:** Draft v1
**Owner:** [you]
**Last updated:** 2026-06-23

> Illustrative numbers (volumes, SLAs, conversion targets) are marked *(illustrative)*. Replace with measured baselines once production data is available.

---

## 1. Problem

EdTech leads arrive across fragmented channels (Meta ads, website forms, WhatsApp, Instagram DMs, manual entry) and lose warmth with every minute they wait for a human response. Three failure modes compound:

1. **Speed-to-lead failure.** Form-filled intent decays sharply within the first 5–10 minutes; counselor follow-up routinely happens hours later.
2. **Consistency failure.** Manual outreach is uneven — some leads get fast, thorough follow-up; others are missed entirely. No system tracks who fell through.
3. **Language access failure.** Outreach defaults to whoever is available, which biases toward English/Hindi and silently filters out leads who would respond better in Telugu, Kannada, Tamil, or Malayalam.

The cost is lost conversions that look like "the lead wasn't interested" but were actually "the lead wasn't reached fast enough, consistently enough, or in their language."

## 2. Goals

- Respond to every new lead within **~10 seconds** *(illustrative)* of arrival, across all channels.
- Reach every lead in their **stated preferred language** (en, hi, te, kn, ta, ml).
- Track every lead through to **payment/conversion or explicit loss** — no silent drop-offs.
- Preserve **human judgment** at decision-heavy steps (screening review, counselor conversation, closing).
- Maintain a complete, queryable **event log** sufficient to train a probabilistic scoring model later.

## 3. Non-goals

- Replacing the counselor for sales conversations or closing.
- Fully automating objection-handling or negotiation.
- Building a custom CRM UI (Google Sheets remains the human-facing system of record).
- Onboarding automation post-payment (invoices, welcome emails, credential provisioning) — future scope.
- Multi-tenant or multi-organization support.

## 4. Users

**Primary:**
- **Lead** — prospective student or parent. Wants a fast, clear, language-appropriate response.
- **Counselor** — works the lead through to enrollment. Wants pre-qualified leads with captured needs, booked into their calendar.

**Secondary:**
- **Sales operations / manager** — needs visibility into pipeline health, stale leads, conversion rate by source/language.

## 5. Scope

### In scope (v1)
- Ingest from Google Sheets (form/manual entry) and a webhook endpoint (programmatic sources).
- Validate and quarantine malformed rows to a `Needs Review` tab.
- Within seconds of ingest: place a multilingual screening voice call, send email, send WhatsApp — in parallel.
- Email and WhatsApp fire regardless of call pickup.
- Screening call: understand needs, book next available counselor slot. Nothing more.
- Auto-assign counselor based on language match and current load.
- Track lead through human-driven stages (`InDiscussion`, `PaymentLinkSent`).
- Mark `Converted` on payment webhook (idempotent). `Lost` branch available at any stage.
- Log every event to a queryable store (currently the `Events` tab; database when needed).

### Out of scope (v1)
- All items in Non-goals above.
- Real-time dashboard UI (status visible in Sheet).
- A/B testing of outreach scripts.
- Inbound call handling (only outbound screening).

## 6. Lead lifecycle

```
New → Contacted → Screened → Assigned → InDiscussion → PaymentLinkSent → Converted
                                              ↓
                                            Lost (branch from any stage)
```

| Status | Trigger | Owned by |
|---|---|---|
| New | Row appears in Sheet / webhook fires | System |
| Contacted | Call attempted + email/WhatsApp sent | System |
| Screened | Call outcome captured (answered / no_answer / voicemail) | System |
| Assigned | Counselor + slot booked | System |
| InDiscussion | Counselor engages lead | Human |
| PaymentLinkSent | Counselor sends payment link | Human |
| Converted | Payment webhook received | System |
| Lost | Manual marking with reason | Human |

## 7. Functional requirements

### FR-1 Ingestion
- Sources: Google Sheets (`onFormSubmit`/`onEdit` trigger), HTTP webhook (Meta ad lead form, programmatic sources).
- Required fields: name, phone (E.164 or 10-digit Indian format), preferred_language.
- Optional fields: email, source, raw_notes.
- Invalid rows are quarantined with a `validation_error`. Pipeline does not crash on bad input.
- Lead is assigned a stable `lead_id` (8-char UUID prefix).

### FR-2 Instant outreach
- All three actions (call, email, WhatsApp) initiated within **10 seconds** *(illustrative)* of `New` → `Contacted` transition.
- Email and WhatsApp use a default attachment set (brochure, schedule, differentiators), language-localized.
- Email skipped only if no email address present.
- WhatsApp always attempted (phone is required).

### FR-3 Screening call
- Voice agent operates in the lead's `preferred_language`. Auto-switches if the lead responds in a different one.
- Call goal: greet by name, briefly explain offering, capture stated needs, offer next available counselor slot. Target call length ≤ 90 seconds.
- Outcome captured: `answered` / `no_answer` / `voicemail`. If answered, `needs_captured` populated.
- Human handoff: if the lead requests a person, transfer immediately.

### FR-4 Scoring
- Rule-based score 0–100, computed after screening.
- Inputs: source weight, call result weight, email presence, needs captured.
- Score is transparent and explainable. Every input is logged for future ML model training.

### FR-5 Counselor assignment
- Prefer counselors who speak the lead's preferred language.
- Among matches, pick lowest current load.
- Fall back to any available counselor if no language match.
- If no counselor is available, status stays at `Screened` and an `assignment_failed` event is logged — lead is never silently dropped.

### FR-6 Conversion
- Payment webhook from provider (Razorpay/Stripe-style) marks the lead `Converted`.
- Handler is **idempotent** by `payment_id`. Duplicate webhooks are logged and ignored, never re-processed.

### FR-7 Loss tracking
- Any stage can transition to `Lost` with a free-text reason.
- Counselors mark `Lost` manually.
- (Future) Automated `Lost` marking for leads unreachable beyond a threshold.

### FR-8 Event logging
- Every state transition and external action produces an event: `lead_id`, `timestamp`, `event_type`, `details`.
- Event types include: `lead_created`, `email_sent`, `whatsapp_sent`, `call_result`, `scored`, `counselor_assigned`, `assignment_failed`, `payment_received`, `payment_duplicate_ignored`, `marked_lost`, `status_changed`.
- Events are the source for offline analytics and (later) ML scoring features.

## 8. Non-functional requirements

| ID | Requirement | Target *(illustrative)* |
|---|---|---|
| NFR-1 | Speed-to-first-touch | ≤ 10 seconds, p95 |
| NFR-2 | Pipeline throughput | 500 leads / month, peak burst 50 / hour |
| NFR-3 | Language coverage | en, hi, te, kn, ta, ml — all gracefully handled |
| NFR-4 | Idempotency | Duplicate webhooks never cause double-conversion |
| NFR-5 | Observability | Every action emits a structured event |
| NFR-6 | Demo runnability | `python -m scripts.run_demo` works with zero credentials |
| NFR-7 | Resilience to bad data | Malformed rows quarantined, never crash the pipeline |
| NFR-8 | Cost (live) | Outreach cost per lead within a per-lead budget (TBD with finance) |

## 9. Constraints and assumptions

- Google Sheets is the human-facing system of record. The team does not want a new tool to learn.
- Phone numbers are predominantly Indian (E.164 with +91 or 10-digit).
- Voice provider (Bolna AI) supports the required Indian-language voice agents.
- Counselors update lead status in the Sheet directly during the human zone.
- Payment provider supports webhook delivery with signed payloads.

## 10. Success metrics

**North-star (illustrative):** conversion rate from `New` to `Converted` lifts by ≥ X% over the baseline (measured pre-LeadFlow).

**Supporting:**
- p95 time from `New` to first outreach attempt ≤ 10 seconds.
- ≥ 95% of leads receive outreach in their preferred language.
- 0 leads end up in `New` for longer than 1 minute (other than quarantined).
- < 1% duplicate payment events successfully double-process.

All metrics are computed from the event log.

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Voice provider downtime / failure | Email + WhatsApp still fire; call retry policy with dead-letter |
| Sheet edited by hand in ways that break the schema | Quarantine to `Needs Review`, never crash |
| Counselor doesn't update status during human zone | Stale-lead sweep (future): flags leads stuck mid-funnel |
| Payment webhook arrives twice | Idempotency by `payment_id` |
| Lead's stated language is wrong | Voice agent auto-switches if the lead replies in another supported language |
| Bolna API costs scale faster than expected | Per-lead budget cap; alert when monthly run-rate exceeds threshold |
| Sheets API rate limits under burst | Batch writes; add a lightweight internal store when this becomes painful |

## 12. Out of band / dependencies

- **Telephony compliance:** outbound calling in India is subject to TRAI / DLT regulations for business calls. Operational/legal sign-off required before live calling.
- **WhatsApp Business API:** template messages must be pre-approved by Meta before live use.
- **Payment provider:** webhook signing secret and idempotency contract must be confirmed in provider docs.

## 13. Roadmap (post-v1)

Triggered by concrete need, not preemptively:

1. **Internal database (Postgres/SQLite).** When Sheets-as-event-log starts hitting API rate limits or becomes too slow for analytics.
2. **ML-based scoring.** Once enough labeled (converted vs. lost) leads are logged.
3. **Stale-lead recovery sweep.** Scheduled job that flags leads stuck mid-funnel and notifies a manager.
4. **Post-payment automation.** Invoice generation, onboarding email with credentials.
5. **Real-time dashboard.** Manager view of pipeline health, conversion by source/language.

## 14. Open questions

- Outreach cost-per-lead budget — needs finance input.
- Counselor escalation policy when no language-matched counselor is available — accept fallback, or hold the lead?
- Retention policy for the event log — how long do we keep raw screening transcripts?
- WhatsApp template inventory — who owns content and approval?

## 15. Glossary

- **Speed-to-lead** — elapsed time from lead arrival to first outreach attempt.
- **Screening call** — automated outbound call to capture needs and book counselor slot.
- **Human zone** — pipeline stages where a counselor is the actor (`InDiscussion`, `PaymentLinkSent`).
- **Adapter** — pluggable implementation of an external-service interface; swapped at config time to switch between mock and live.
