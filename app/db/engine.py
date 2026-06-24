"""
Async SQLAlchemy engine + session factory.

The DB is LeadFlow's system of record for lead state, idempotency, and
outreach durability. Sheets remain a human-facing projection - see
app/adapters/db_store.py.

Single dedicated event loop:
    Every coroutine that touches `engine` (DbStore's per-call bridge, the
    reconciler loop, run_outreach_durable's claim/mark helpers) must run on
    the *same* event loop, via run_db()/run_db_async() below — never call
    engine_module.async_session() directly from your own loop.

    Why: those call sites run on different threads with their own event
    loops (DbStore's sync methods use asyncio.run() per call; the reconciler
    runs as a task on the main loop; webhook-triggered outreach runs via a
    fresh asyncio.run() per BackgroundTask). SQLAlchemy's async connection
    pool is not safe to touch concurrently from two different event loops —
    it deadlocks (one loop's `select()` waits forever for a readiness signal
    that belongs to a different loop). Funneling every access through one
    persistent background loop removes the cross-loop concurrency entirely.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Coroutine, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url)
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Start the dedicated DB event loop thread on first use (idempotent)."""
    global _loop
    with _loop_lock:
        if _loop is not None:
            return _loop

        ready = threading.Event()
        started: list[asyncio.AbstractEventLoop] = []

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            started.append(loop)
            ready.set()
            loop.run_forever()

        threading.Thread(target=_run, name="db-event-loop", daemon=True).start()
        ready.wait()
        _loop = started[0]
        return _loop


def run_db(coro: Coroutine[Any, Any, T]) -> T:
    """Run `coro` on the dedicated DB loop and block for its result.

    For sync callers with no event loop of their own (DbStore's public
    methods, called from arbitrary threads).
    """
    future: Future[T] = asyncio.run_coroutine_threadsafe(coro, _ensure_loop())
    return future.result()


async def run_db_async(coro: Coroutine[Any, Any, T]) -> T:
    """Await `coro` on the dedicated DB loop without blocking the caller's
    own loop's thread.

    For async callers running on some other loop (the reconciler, outreach
    claim/mark helpers, app shutdown) that need to cooperatively await.
    """
    future = asyncio.run_coroutine_threadsafe(coro, _ensure_loop())
    return await asyncio.wrap_future(future)


async def dispose_engine() -> None:
    await run_db_async(engine.dispose())
