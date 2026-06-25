"""
Durable outreach runner.

run_outreach_durable() is the BackgroundTask target for /webhook/new-lead.
It wraps the standard run_outreach() call with DB-backed state tracking so
that:

  - Only one worker processes a given lead at a time (optimistic locking via
    the outreach_state column).
  - A crashed or stalled worker leaves the lead in a recoverable state so the
    reconciler (app/reconciler.py) can re-schedule it.
  - After 5 failed attempts the lead is moved to FAILED and the reconciler
    stops picking it up.

outreach_state lifecycle:
    PENDING       — waiting to be processed
    IN_PROGRESS   — a worker has claimed this lead (outreach_started_at set)
    DONE          — outreach completed: at least one channel succeeded
    FAILED        — exhausted MAX_ATTEMPTS (needs human review)
    QUARANTINED   — permanent error; lead sent to Needs Review (terminal,
                    the reconciler only sweeps PENDING/IN_PROGRESS)

run_outreach()'s channels (email/WhatsApp/call) each succeed or fail
independently (see app/pipeline/outreach.py) — a single channel failing is
not a reason to retry the whole lead. After an attempt completes, three
outcomes are possible: a permanent error on any channel quarantines the
lead to Needs Review (terminal — no retry, since the data itself is bad);
otherwise zero channels succeeding retries (outreach achieved nothing —
this also covers a no-email lead whose remaining channels both failed,
which could never reach a literal "all three failed" check); otherwise at
least one channel succeeded and the attempt is marked done. Separately,
run_outreach() itself raising (a real bug or infra failure) always retries,
regardless of permanent/transient — that's an unhandled bug, not a
classified channel error.

Events log lifecycle for one attempt (the audit trail — see
app/pipeline/outreach.py for the per-channel *_sent/*_failed events nested
inside it): outreach_attempt_started -> email_sent/email_failed ->
whatsapp_sent/whatsapp_failed -> call_sent/call_failed ->
outreach_attempt_completed -> (outreach_quarantined OR
outreach_retry_scheduled OR outreach_complete). A raised exception instead
logs outreach_attempt_failed and always retries.

Each attempt also reports to Prometheus (app/metrics.py) via
_observe_outreach_attempt: outreach_attempt_total{outcome} (success /
permanent_error / transient_error — a raised exception counts as
transient_error) and outreach_latency_seconds, the wall-clock delay since
lead.timestamp_created. That's elapsed-since-ingest, not this function's
own runtime — observed on every attempt, including reconciler retries.

IMPORTANT: run_outreach_durable is an async function. The sync pipeline
functions (run_outreach, handle_call_result) are called via
asyncio.get_event_loop().run_in_executor so they run in a thread, off
whatever loop is driving run_outreach_durable. Their store calls bridge to
the DB via app.db.engine.run_db(), which funnels onto one dedicated event
loop — see that module's docstring for why a shared engine can't be touched
from multiple loops directly.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.models import LeadRow
from app.metrics import outreach_attempt_total, outreach_latency_seconds
from app.utils.logging import log

MAX_ATTEMPTS = 5
STALE_THRESHOLD_MINUTES = 2


def _observe_outreach_attempt(lead: Any, outcome: str) -> None:
    """Record this attempt's outcome and its delay since the lead was
    ingested. Observed on every attempt, not just the first — a lead
    retried by the reconciler minutes later still gets observed, so the
    histogram trends upward if retries pile up rather than masking them."""
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(lead.timestamp_created)).total_seconds()
    outreach_latency_seconds.observe(elapsed)
    outreach_attempt_total.labels(outcome=outcome).inc()


async def run_outreach_durable(
    lead_id: str,
    store: Any = None,
    dialer: Any = None,
    email: Any = None,
    whatsapp: Any = None,
) -> None:
    """Claim the lead, run outreach, and update outreach_state atomically.

    Adapters default to mock implementations so the demo and tests work
    without real credentials. In production, main.py passes the real adapters.

    Args:
        lead_id:  The lead to process.
        store:    SheetStore instance. Defaults to a fresh InMemorySheet.
        dialer:   Dialer adapter.
        email:    Email Messenger adapter.
        whatsapp: WhatsApp Messenger adapter.
    """
    from app.adapters.dialer import MockDialer
    from app.adapters.sheets import InMemorySheet
    from app.adapters.whatsapp import MockEmail, MockWhatsApp

    # --- Claim the lead (atomic optimistic-lock) ----------------------------
    now = datetime.now(timezone.utc)
    claimed = await _claim_lead(lead_id, now)
    if not claimed:
        log(f"[outreach_durable] {lead_id}: already claimed by another worker, skipping")
        return

    # --- Build default adapters if not injected (demo/test path) -----------
    if store is None:
        store = InMemorySheet()
    if dialer is None:
        dialer = MockDialer(simulate_delay=False)
    if email is None:
        email = MockEmail()
    if whatsapp is None:
        whatsapp = MockWhatsApp()

    # --- Fetch the lead via store in a thread (store methods bridge via run_db) --
    loop = asyncio.get_running_loop()
    lead = await loop.run_in_executor(None, store.get_lead, lead_id)
    if lead is None:
        log(f"[outreach_durable] {lead_id}: lead not found in store")
        await _mark_done(lead_id)
        return

    async def _log_event(event_type: str, details: str = "") -> None:
        # store.log_event is sync (see app/adapters/db_store.py) — dispatch it
        # off the event loop, same as every other store call in this function.
        await loop.run_in_executor(None, store.log_event, lead_id, event_type, details)

    # --- Run outreach in a thread (avoids asyncio.run-inside-running-loop) -
    await _log_event("outreach_attempt_started")
    try:
        from app.pipeline.outreach import OutreachResult, handle_call_result, run_outreach

        def _do_outreach() -> OutreachResult:
            result = run_outreach(lead, store, dialer, email, whatsapp)
            if "call" not in result.channel_failures:
                handle_call_result(lead, lead.call_result, lead.needs_captured, store)
            return result

        result = await loop.run_in_executor(None, _do_outreach)
        channel_failures = result.channel_failures
        channel_successes = result.channel_successes
        await _log_event(
            "outreach_attempt_completed",
            f"channel_successes={sorted(channel_successes)}, channel_failures={sorted(channel_failures)}",
        )

        if result.permanent_error:
            outcome = "permanent_error"
        elif not channel_successes:
            outcome = "transient_error"
        else:
            outcome = "success"
        _observe_outreach_attempt(lead, outcome)

        if outcome == "permanent_error":
            # Permanent error — quarantine to Needs Review, don't retry.
            await _log_event("outreach_quarantined", "permanent error detected")
            await asyncio.to_thread(
                store.quarantine,
                lead.to_row(),
                f"Outreach failed with permanent error: {', '.join(sorted(channel_failures))}",
            )
            await _mark_quarantined(lead_id)
            log(f"[outreach_durable] {lead_id}: permanent error — quarantined")
        elif outcome == "transient_error":
            # Outreach achieved nothing — that's the one case the reconciler
            # should retry. One working channel still counts as a successful
            # attempt.
            log(f"[outreach_durable] {lead_id}: no channel succeeded — retrying")
            attempts = await _mark_failed_or_retry(lead_id)
            await _log_event("outreach_retry_scheduled", f"attempt={attempts}")
        else:
            await _mark_done(lead_id)
            await _log_event("outreach_complete")
            log(f"[outreach_durable] {lead_id}: outreach DONE (channel_failures={sorted(channel_failures)})")
    except Exception as exc:  # noqa: BLE001
        # run_outreach() itself raising (a bug or infra failure) — always
        # retries, same treatment as a transient channel failure.
        _observe_outreach_attempt(lead, "transient_error")
        await _log_event("outreach_attempt_failed", f"{type(exc).__name__}: {exc}")
        log(f"[outreach_durable] {lead_id}: outreach FAILED — {exc}")
        attempts = await _mark_failed_or_retry(lead_id)
        await _log_event("outreach_retry_scheduled", f"attempt={attempts}")


# ---------------------------------------------------------------------------
# Private DB helpers — bridge to the dedicated DB loop via run_db_async()
# ---------------------------------------------------------------------------

async def _claim_lead(lead_id: str, now: datetime) -> bool:
    """Atomically set outreach_state=IN_PROGRESS for a PENDING or stale
    IN_PROGRESS lead. Returns True if the claim succeeded."""
    from sqlalchemy import update  # noqa: PLC0415
    from app.db.engine import run_db_async  # noqa: PLC0415

    stale_before = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    async def _claim() -> bool:
        import app.db.engine as engine_module  # noqa: PLC0415 — lazy import for test patching
        async with engine_module.async_session() as session:
            result = await session.execute(
                update(LeadRow)
                .where(
                    LeadRow.lead_id == lead_id,
                    (
                        (LeadRow.outreach_state == "PENDING")
                        | (
                            (LeadRow.outreach_state == "IN_PROGRESS")
                            & (LeadRow.outreach_started_at < stale_before)
                        )
                    ),
                )
                .values(
                    outreach_state="IN_PROGRESS",
                    outreach_started_at=now,
                    outreach_attempts=LeadRow.outreach_attempts + 1,
                )
            )
            await session.commit()
            return result.rowcount > 0

    # Funneled through the dedicated DB loop (app/db/engine.py) — this
    # function runs on whichever loop called run_outreach_durable (the main
    # loop via the reconciler, or a fresh per-call loop via the BackgroundTask
    # shim in main.py), either of which would otherwise touch the shared
    # engine concurrently with another loop and deadlock.
    return await run_db_async(_claim())


async def _mark_done(lead_id: str) -> None:
    from sqlalchemy import update  # noqa: PLC0415
    from app.db.engine import run_db_async  # noqa: PLC0415

    async def _mark() -> None:
        import app.db.engine as engine_module  # noqa: PLC0415
        async with engine_module.async_session() as session:
            await session.execute(
                update(LeadRow)
                .where(LeadRow.lead_id == lead_id)
                .values(outreach_state="DONE")
            )
            await session.commit()

    await run_db_async(_mark())


async def _mark_quarantined(lead_id: str) -> None:
    """Mark a lead as quarantined — terminal state, the reconciler won't
    pick it up again (it only sweeps PENDING/IN_PROGRESS)."""
    from sqlalchemy import update  # noqa: PLC0415
    from app.db.engine import run_db_async  # noqa: PLC0415

    async def _mark() -> None:
        import app.db.engine as engine_module  # noqa: PLC0415
        async with engine_module.async_session() as session:
            await session.execute(
                update(LeadRow)
                .where(LeadRow.lead_id == lead_id)
                .values(outreach_state="QUARANTINED")
            )
            await session.commit()

    await run_db_async(_mark())


async def _mark_failed_or_retry(lead_id: str) -> int:
    """On failure: reset to PENDING for reconciler retry, or set FAILED after
    MAX_ATTEMPTS exhausted. Returns the attempt count, for the caller's
    outreach_retry_scheduled log event."""
    from app.db.engine import run_db_async  # noqa: PLC0415

    async def _mark() -> int:
        import app.db.engine as engine_module  # noqa: PLC0415
        async with engine_module.async_session() as session:
            row = await session.get(LeadRow, lead_id)
            if row is None:
                return 0
            if row.outreach_attempts >= MAX_ATTEMPTS:
                row.outreach_state = "FAILED"
                log(f"[outreach_durable] {lead_id}: max attempts reached → FAILED")
            else:
                row.outreach_state = "PENDING"
            await session.commit()
            return row.outreach_attempts

    return await run_db_async(_mark())
