"""
Outreach reconciler — durable BackgroundTask recovery.

The reconciler runs every 30 seconds and looks for leads stuck in a bad
outreach_state:
  - PENDING with outreach_started_at < now - 2 min (never started, or failed)
  - IN_PROGRESS with outreach_started_at < now - 2 min (worker died mid-flight)

For each stale lead it re-schedules run_outreach_durable() so the outreach
eventually completes without manual intervention.

The reconciler is started as an asyncio background task during app lifespan
(app/main.py) and cancelled cleanly on shutdown.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import LeadRow
from app.utils.logging import log

RECONCILER_INTERVAL_SECONDS = 30
STALE_THRESHOLD_MINUTES = 2
MAX_ATTEMPTS = 5

_inflight_outreach: set[asyncio.Task] = set()


async def reconcile_outreach() -> None:
    """Loop forever, re-scheduling stale outreach leads every 30 seconds."""
    while True:
        try:
            await _reconcile_once()
        except Exception as exc:  # noqa: BLE001
            log(f"[reconciler] error: {exc}")
        await asyncio.sleep(RECONCILER_INTERVAL_SECONDS)


async def _reconcile_once() -> None:
    """Single reconciler pass: find and re-schedule stale leads."""
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    import app.db.engine as engine_module  # noqa: PLC0415 — lazy import for test patching
    async with engine_module.async_session() as session:
        result = await session.execute(
            select(LeadRow).where(
                LeadRow.outreach_state.in_(["PENDING", "IN_PROGRESS"]),
                LeadRow.outreach_started_at < stale_before,
            )
        )
        stale_leads = result.scalars().all()

    if not stale_leads:
        return

    log(f"[reconciler] found {len(stale_leads)} stale lead(s) — re-scheduling")

    # Import here to avoid circular import (outreach imports from adapters,
    # reconciler is imported by main.py which also imports outreach).
    from app.pipeline.outreach_durable import run_outreach_durable  # noqa: PLC0415

    for lead_row in stale_leads:
        task = asyncio.create_task(
            run_outreach_durable(lead_row.lead_id),
            name=f"outreach-{lead_row.lead_id}",
        )
        _inflight_outreach.add(task)
        task.add_done_callback(_inflight_outreach.discard)


async def drain_inflight(timeout: float = 10.0) -> None:
    """Wait for in-flight outreach workers spawned by the reconciler to
    finish their DB transactions before the engine is disposed.

    Bounded by `timeout` so shutdown never hangs on a stuck worker — any
    still-pending tasks are left to the reconciler-on-restart recovery path.
    """
    if not _inflight_outreach:
        return

    pending = list(_inflight_outreach)
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), timeout=timeout
        )
    except asyncio.TimeoutError:
        remaining = len(_inflight_outreach)
        log(f"[reconciler] drain_inflight timed out with {remaining} task(s) still pending")
