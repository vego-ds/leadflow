# Durability Sprint Brief

We're addressing the audit findings on durability, concurrency, idempotency, and webhook security. Read CLAUDE.md and the skills in .claude/skills/ before starting.

## Architectural decisions for this sprint (do not deviate without asking)

- **Persistent store:** SQLite with SQLAlchemy 2.0 async + aiosqlite. Alembic for migrations. No raw SQL. No Postgres-specific features.
- **Task queue: none.** We keep FastAPI BackgroundTasks and make them durable via a DB-backed `pending_outreach` row + a reconciler loop. No Redis, no ARQ, no RQ. If you think we need a queue, stop and ask before adding one.
- **Sheets remains the human-facing view.** The DB is the system of record for lead state and idempotency.

## Ground rules

- One commit per task. Run `pytest -q` and `python -m scripts.run_demo` after each. Both must pass before moving on.
- Follow adapter-discipline, test-discipline, commit-hygiene, secrets-discipline, python-style throughout.
- If anything is ambiguous or you'd deviate from this brief, ask before changing direction.

## Task 1 — Introduce SQLite + SQLAlchemy + Alembic

- Add `sqlalchemy>=2.0`, `aiosqlite`, `alembic`, `pydantic-settings` to requirements.txt.
- Create `app/db/` with:
  - `engine.py` — async engine + sessionmaker pointed at `sqlite+aiosqlite:///./leadflow.db` (path from settings).
  - `models.py` — SQLAlchemy ORM models: `LeadRow`, `EventRow`, `CounselorRow`, `ProcessedWebhook` (columns: `webhook_type`, `external_id`, `processed_at`; unique on `(webhook_type, external_id)`).
  - `base.py` — declarative base.
- Add `app/config.py` using pydantic-settings: loads `DATABASE_URL`, `WEBHOOK_SIGNING_SECRET`, `BOLNA_WEBHOOK_SECRET` from `.env`. Update `.env.example` with these.
- Initialize Alembic in `migrations/`. Create the initial migration with all four tables.
- Add `leadflow.db` and `migrations/__pycache__/` to `.gitignore`.
- Wire app startup to run pending migrations on boot (Alembic programmatic API), and shutdown to dispose the engine cleanly.

**Commit:** `feat: add SQLite + SQLAlchemy async + Alembic foundation`

## Task 2 — Move lead state and counselors into the DB; Sheets becomes a projection

- Add a new adapter `app/adapters/db_store.py` implementing `SheetStore` against the DB (`DbStore`). Keep `InMemorySheet` for tests. Keep `GoogleSheet` stub as-is.
- `DbStore.append_lead`/`update_lead`/`log_event`/`quarantine` write to DB. `get_counselors`/`update_counselor` read/write `CounselorRow`.
- `SheetStore` interface gains `record_processed_webhook(webhook_type, external_id) -> bool` (returns True if newly recorded, False if duplicate). Implement on all three. The DB implementation uses a unique-constraint insert with an exception catch — must be atomic.
- Migration script: `scripts/seed_counselors.py` loads `data/counselors.json` into the DB.
- Wire `app/main.py` to use `DbStore` by default. `InMemorySheet` stays for tests only.
- Keep Sheets writes — but document clearly in the file header that the DB is the system of record; Sheets is a projection for humans.

**Commit:** `feat: DB-backed SheetStore; counselors and idempotency persisted`

## Task 3 — Replace in-memory idempotency with DB-backed idempotency

- Remove `_processed_payments` set from `app/pipeline/conversion.py`.
- `handle_payment` calls `store.record_processed_webhook("payment", payment_id)`. If False, log `payment_duplicate_ignored` and return without state change.
- Add the same pattern to `/webhook/call-result` (Task 5): dedupe by `call_id`.

**Commit:** `refactor: persistent idempotency for payment and call-result webhooks`

## Task 4 — Durable BackgroundTasks with a reconciler

- Add an `outreach_state` column to `LeadRow`: `PENDING`, `IN_PROGRESS`, `DONE`, `FAILED`. Default `PENDING` on insert. Add `outreach_started_at`, `outreach_attempts` columns.
- `/webhook/new-lead`:
  - Validate + ingest synchronously → DB row with `outreach_state=PENDING`.
  - Schedule BackgroundTasks to call `run_outreach_durable(lead_id)`.
  - Return 202.
- `run_outreach_durable(lead_id)`:
  - Atomic update: set `outreach_state=IN_PROGRESS`, `outreach_started_at=now`, `outreach_attempts=outreach_attempts+1` — only if current state is `PENDING` or stale `IN_PROGRESS` (>2 min old). If no rows affected, return (another worker has it).
  - Run outreach. On success: `outreach_state=DONE`. On failure: `outreach_state=PENDING` (so reconciler retries), unless `outreach_attempts >= 5` then `FAILED`.
- Add `app/reconciler.py` with `async def reconcile_outreach()` that every 30s finds leads where `outreach_state IN ('PENDING','IN_PROGRESS') AND outreach_started_at < now() - 2 minutes`, and re-schedules them.
- Start the reconciler as an `asyncio.create_task` on app startup; cancel on shutdown.

**Commit:** `feat: durable BackgroundTasks via DB state + reconciler loop`

## Task 5 — State-machine guard + idempotent /webhook/call-result

- Add `app/pipeline/transitions.py` defining legal `LeadStatus` transitions as a dict. Add `assert_transition(current, next)` that raises `IllegalTransition` on violation.
- Every place that mutates `lead.status` goes through a single helper `set_status(lead, next_status, store)` which checks the transition, updates, logs the event.
- `/webhook/call-result`:
  - Verify HMAC (Task 6).
  - `call_id` required in payload. Call `store.record_processed_webhook("call_result", call_id)`. If duplicate, return 200 with `{"status": "already_processed"}`.
  - Look up lead. If not found → 404.
  - If current status not in `{CONTACTED}` → 409 with reason. (Late/replayed callback shouldn't move a converted lead backward.)
  - Otherwise: update `call_result`, `needs_captured`, transition to `SCREENED`, score, assign counselor.

**Commit:** `feat: state-machine guards + idempotent call-result handler`

## Task 6 — HMAC signing + replay window on both webhooks

- Add `app/security/webhook_auth.py`:
  - `verify_signature(payload_bytes, signature_header, timestamp_header, secret, max_age_seconds=300)`.
  - Uses HMAC-SHA256 over `f"{timestamp}.{payload}"`. Constant-time compare via `hmac.compare_digest`. Reject if timestamp older than `max_age_seconds`.
- Add a FastAPI dependency `require_signed_webhook(secret_setting_name)` and apply it to both `/webhook/new-lead` and `/webhook/call-result`. Read the secret name from settings.
- Update `apps_script/trigger.gs` to sign requests: HMAC-SHA256 of `timestamp.body`, send as headers `X-LeadFlow-Signature` and `X-LeadFlow-Timestamp`. Use `PropertiesService.getScriptProperties().getProperty('WEBHOOK_SIGNING_SECRET')` — do not hardcode the secret.
- For the Bolna webhook: leave a clear TODO with a doc comment pointing to `bolna_agent/agent_config.json` — note that Bolna's signing scheme needs to be confirmed against their docs and we'll adjust the verifier accordingly.

**Commit:** `feat: HMAC signature verification + replay window on webhooks`

## Task 7 — Tests for the new guarantees

Add tests covering:

- Concurrent assignment: spawn 5 leads in parallel via `asyncio.gather`, assert no counselor exceeds expected load and no counselor is double-assigned to the same lead.
- Crash-mid-outreach replay: insert a lead in `IN_PROGRESS` with `outreach_started_at` 5 min old, run reconciler, assert it gets re-scheduled and completes.
- Duplicate call-result: post twice with the same `call_id`, assert second returns `already_processed` and no second `counselor_assigned` event is logged.
- Replayed webhook (old timestamp): post with timestamp 10 min old, assert 401.
- Forged signature: post with bad signature, assert 401.
- Illegal transition: call `set_status(converted_lead, SCREENED, ...)`, assert `IllegalTransition`.

All tests use `TestClient` for the HTTP-level cases. Use `InMemorySheet` only for unit tests of pure pipeline functions; the durability/idempotency tests must use the DB-backed store against a temp SQLite file.

**Commit:** `test: cover concurrency, durability, idempotency, webhook auth, transitions`

## After all seven tasks

- `pytest -q` all green. `python -m scripts.run_demo` runs clean.
- Update `docs/PRD.md`:
  - Mark deferred items resolved.
  - Note remaining deferred work: structured logging, metrics, PII policy, Postgres migration, real task queue.
- Update `README.md`: add a one-line note that the system uses SQLite for state of record, with Sheets as a human-facing projection.

**Final commit if needed:** `docs: update PRD and README to reflect durability sprint`
