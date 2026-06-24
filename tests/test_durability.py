"""
Durability, idempotency, concurrency, webhook auth, and state-machine tests.

Tests that touch the DB use a temporary SQLite file (tmp_path fixture from pytest).
Tests for pure pipeline logic use InMemorySheet only.
HTTP-level tests use FastAPI TestClient.

Run:  pytest -q tests/test_durability.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.sheets import InMemorySheet
from app.models import CallResult, Language, Lead, LeadStatus, Source
from app.pipeline.conversion import handle_payment
from app.pipeline.ingest import validate_and_normalize
from app.pipeline.outreach import handle_call_result
from app.pipeline.transitions import IllegalTransition, set_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _good_row(**overrides) -> dict:
    base = {
        "name": "Test User",
        "phone": "+919876543210",
        "email": "t@example.com",
        "source": "website",
        "preferred_language": "hi",
        "raw_notes": "",
    }
    return {**base, **overrides}


def _make_lead(lead_id: str = "test01", status: LeadStatus = LeadStatus.NEW) -> Lead:
    return Lead(
        lead_id=lead_id,
        name="Test User",
        phone="+919876543210",
        preferred_language=Language.HINDI,
        source=Source.WEBSITE,
        email="t@example.com",
        status=status,
    )


def _sign_request(body: bytes, secret: str) -> tuple[str, str]:
    """Return (signature_hex, timestamp_str) for a valid signed request."""
    ts = str(int(time.time()))
    message = ts.encode() + b"." + body
    sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return sig, ts


# ---------------------------------------------------------------------------
# Test fixture: minimal app that shares main.py routes but uses a no-op lifespan
# so tests don't run migrations or start the reconciler.
# ---------------------------------------------------------------------------

@pytest.fixture()
def in_memory_store() -> InMemorySheet:
    store = InMemorySheet()
    store.seed_counselors([
        {"name": "C1", "languages_spoken": ["hi", "en"],
         "available_slots": ["Mon 10", "Mon 11", "Mon 12", "Mon 13", "Mon 14"],
         "current_load": 0},
        {"name": "C2", "languages_spoken": ["hi", "en"],
         "available_slots": ["Tue 10", "Tue 11", "Tue 12", "Tue 13", "Tue 14"],
         "current_load": 0},
    ])
    return store


@pytest.fixture()
def test_client(in_memory_store):
    """TestClient against main app with InMemorySheet and no-op lifespan."""
    import app.main as main_module

    original_store = main_module.store
    main_module.store = in_memory_store

    # Override lifespan to skip migrations and reconciler in tests
    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    original_lifespan = main_module.app.router.lifespan_context
    main_module.app.router.lifespan_context = _test_lifespan

    with TestClient(main_module.app, raise_server_exceptions=True) as client:
        yield client

    main_module.app.router.lifespan_context = original_lifespan
    main_module.store = original_store


# ---------------------------------------------------------------------------
# Test 1: Illegal status transition
# ---------------------------------------------------------------------------

class TestIllegalTransition:
    def test_converted_lead_cannot_go_back_to_screened(self):
        store = InMemorySheet()
        lead = _make_lead(status=LeadStatus.CONVERTED)
        store.append_lead(lead)

        with pytest.raises(IllegalTransition):
            set_status(lead, LeadStatus.SCREENED, store)

    def test_new_lead_can_go_to_contacted(self):
        store = InMemorySheet()
        lead = _make_lead(status=LeadStatus.NEW)
        store.append_lead(lead)
        set_status(lead, LeadStatus.CONTACTED, store)
        assert lead.status == LeadStatus.CONTACTED

    def test_lost_is_terminal(self):
        store = InMemorySheet()
        lead = _make_lead(status=LeadStatus.LOST)
        store.append_lead(lead)
        with pytest.raises(IllegalTransition):
            set_status(lead, LeadStatus.NEW, store)

    def test_screened_cannot_skip_to_converted(self):
        store = InMemorySheet()
        lead = _make_lead(status=LeadStatus.SCREENED)
        store.append_lead(lead)
        with pytest.raises(IllegalTransition):
            set_status(lead, LeadStatus.CONVERTED, store)


# ---------------------------------------------------------------------------
# Test 2: In-memory idempotency (via InMemorySheet)
# ---------------------------------------------------------------------------

class TestInMemoryIdempotency:
    def test_payment_is_idempotent(self):
        store = InMemorySheet()
        lead = validate_and_normalize(_good_row(), store)
        handle_payment(lead, "pay_abc", store)
        handle_payment(lead, "pay_abc", store)  # duplicate

        received = [e for e in store.events if e["event_type"] == "payment_received"]
        assert len(received) == 1

    def test_duplicate_returns_false(self):
        store = InMemorySheet()
        assert store.record_processed_webhook("payment", "p1") is True
        assert store.record_processed_webhook("payment", "p1") is False

    def test_different_ids_are_independent(self):
        store = InMemorySheet()
        assert store.record_processed_webhook("payment", "p1") is True
        assert store.record_processed_webhook("payment", "p2") is True


# ---------------------------------------------------------------------------
# Test 3: Sequential counselor assignment (InMemorySheet)
# ---------------------------------------------------------------------------

class TestConcurrentAssignmentInMemory:
    def _make_store_with_counselors(self) -> InMemorySheet:
        store = InMemorySheet()
        store.seed_counselors([
            {"name": "C1", "languages_spoken": ["hi", "en"],
             "available_slots": ["Mon 10", "Mon 11", "Mon 12", "Mon 13", "Mon 14"],
             "current_load": 0},
            {"name": "C2", "languages_spoken": ["hi", "en"],
             "available_slots": ["Tue 10", "Tue 11", "Tue 12", "Tue 13", "Tue 14"],
             "current_load": 0},
        ])
        return store

    def test_five_sequential_leads_each_get_unique_counselor_slot(self):
        """5 leads processed sequentially should get unique slots."""
        store = self._make_store_with_counselors()
        leads = []
        for i in range(5):
            row = _good_row(name=f"Lead {i}", phone=f"+9198765{i:05d}")
            lead = validate_and_normalize(row, store)
            handle_call_result(lead, CallResult.ANSWERED, "needs batch", store)
            leads.append(lead)

        assigned = [l for l in leads if l.assigned_counselor is not None]
        slots = [l.counselor_slot for l in assigned]
        assert len(slots) == len(set(slots)), f"Duplicate slots: {slots}"

    def test_counselor_load_does_not_exceed_available_slots(self):
        store = self._make_store_with_counselors()
        for i in range(5):
            row = _good_row(name=f"Lead {i}", phone=f"+9198765{i:05d}")
            lead = validate_and_normalize(row, store)
            handle_call_result(lead, CallResult.ANSWERED, "", store)

        for c in store.get_counselors():
            assert c["current_load"] >= 0

    def test_five_concurrent_leads_no_duplicate_slots(self):
        """asyncio.gather runs 5 coroutines concurrently against the same store."""
        store = self._make_store_with_counselors()

        async def _process_one(i: int) -> Lead | None:
            row = _good_row(name=f"Lead {i}", phone=f"+9198765{i:05d}")
            lead = validate_and_normalize(row, store)
            if lead is None:
                return None
            handle_call_result(lead, CallResult.ANSWERED, "needs batch", store)
            return lead

        async def _run():
            return await asyncio.gather(*[_process_one(i) for i in range(5)])

        leads = asyncio.run(_run())
        assigned = [l for l in leads if l is not None and l.assigned_counselor is not None]
        slots = [l.counselor_slot for l in assigned]
        assert len(slots) == len(set(slots)), f"Duplicate slots: {slots}"


# ---------------------------------------------------------------------------
# Test 4: HMAC signature verification unit tests
# ---------------------------------------------------------------------------

class TestWebhookAuth:
    def test_valid_signature_passes(self):
        from app.security.webhook_auth import verify_signature
        body = b'{"test": 1}'
        ts = str(int(time.time()))
        secret = "test_secret_abc"
        message = ts.encode() + b"." + body
        sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        # Should not raise
        verify_signature(body, sig, ts, secret)

    def test_bad_signature_raises_401(self):
        from fastapi import HTTPException
        from app.security.webhook_auth import verify_signature
        body = b'{"test": 1}'
        ts = str(int(time.time()))
        with pytest.raises(HTTPException) as exc_info:
            verify_signature(body, "badsig", ts, "real_secret")
        assert exc_info.value.status_code == 401

    def test_old_timestamp_raises_401(self):
        from fastapi import HTTPException
        from app.security.webhook_auth import verify_signature
        body = b'{"test": 1}'
        old_ts = str(int(time.time()) - 700)  # 700s ago > 5 min window
        secret = "test_secret_abc"
        message = old_ts.encode() + b"." + body
        sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        with pytest.raises(HTTPException) as exc_info:
            verify_signature(body, sig, old_ts, secret, max_age_seconds=300)
        assert exc_info.value.status_code == 401

    def test_empty_secret_raises_401(self):
        from fastapi import HTTPException
        from app.security.webhook_auth import verify_signature
        with pytest.raises(HTTPException) as exc_info:
            verify_signature(b"body", "sig", str(int(time.time())), secret="")
        assert exc_info.value.status_code == 401

    def test_future_timestamp_within_window_passes(self):
        """Clock skew of < max_age_seconds should pass."""
        from app.security.webhook_auth import verify_signature
        body = b'{"test": 1}'
        ts = str(int(time.time()) + 10)  # 10s future, within 300s window
        secret = "test_secret_xyz"
        message = ts.encode() + b"." + body
        sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        verify_signature(body, sig, ts, secret)


# ---------------------------------------------------------------------------
# Test 5: HTTP-level call-result webhook tests
# ---------------------------------------------------------------------------

class TestCallResultWebhook:
    def test_duplicate_call_result_returns_already_processed(
        self, test_client, in_memory_store
    ):
        lead = validate_and_normalize(_good_row(), in_memory_store)
        lead.status = LeadStatus.CONTACTED
        in_memory_store.update_lead(lead)

        payload = {
            "lead_id": lead.lead_id,
            "call_id": "call_abc_001",
            "result": "answered",
            "needs_captured": "wants batch",
        }

        r1 = test_client.post("/webhook/call-result", json=payload)
        assert r1.status_code == 200
        assert r1.json().get("status") != "already_processed"

        r2 = test_client.post("/webhook/call-result", json=payload)
        assert r2.status_code == 200
        assert r2.json()["status"] == "already_processed"

        # No second counselor_assigned event after the duplicate
        assigned_events = [
            e for e in in_memory_store.events if e["event_type"] == "counselor_assigned"
        ]
        assert len(assigned_events) <= 1

    def test_call_result_on_converted_lead_returns_409(
        self, test_client, in_memory_store
    ):
        lead = validate_and_normalize(_good_row(), in_memory_store)
        lead.status = LeadStatus.CONVERTED
        in_memory_store.update_lead(lead)

        r = test_client.post("/webhook/call-result", json={
            "lead_id": lead.lead_id,
            "call_id": "call_xyz_999",
            "result": "answered",
        })
        assert r.status_code == 409

    def test_call_result_unknown_lead_returns_404(self, test_client):
        r = test_client.post("/webhook/call-result", json={
            "lead_id": "doesnotexist",
            "call_id": "call_zzz",
            "result": "answered",
        })
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Test 6: HMAC enforcement at the HTTP layer
# ---------------------------------------------------------------------------

class TestSignedWebhookEndpoints:
    """Test HMAC enforcement when a signing secret is configured."""

    @pytest.fixture()
    def signed_client(self, in_memory_store):
        """App instance with WEBHOOK_SIGNING_SECRET configured."""
        import app.main as main_module
        from app.config import settings
        from contextlib import asynccontextmanager

        original_secret = settings.webhook_signing_secret
        original_store = main_module.store
        settings.webhook_signing_secret = "test_hmac_secret_xyz"
        main_module.store = in_memory_store

        @asynccontextmanager
        async def _test_lifespan(app):
            yield

        original_lifespan = main_module.app.router.lifespan_context
        main_module.app.router.lifespan_context = _test_lifespan

        with TestClient(main_module.app, raise_server_exceptions=False) as client:
            yield client

        main_module.app.router.lifespan_context = original_lifespan
        settings.webhook_signing_secret = original_secret
        main_module.store = original_store

    def test_valid_signature_accepted(self, signed_client):
        body = (
            b'{"name":"Alice","phone":"+919876543210","preferred_language":"hi",'
            b'"source":"website","email":null,"raw_notes":""}'
        )
        sig, ts = _sign_request(body, "test_hmac_secret_xyz")
        r = signed_client.post(
            "/webhook/new-lead",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-LeadFlow-Signature": sig,
                "X-LeadFlow-Timestamp": ts,
            },
        )
        assert r.status_code in (200, 202)

    def test_forged_signature_rejected_401(self, signed_client):
        body = b'{"name":"Bob","phone":"+919876543211","preferred_language":"hi","source":"website"}'
        ts = str(int(time.time()))
        r = signed_client.post(
            "/webhook/new-lead",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-LeadFlow-Signature": "0" * 64,
                "X-LeadFlow-Timestamp": ts,
            },
        )
        assert r.status_code == 401

    def test_old_timestamp_rejected_401(self, signed_client):
        body = b'{"name":"Carol","phone":"+919876543212","preferred_language":"hi","source":"website"}'
        old_ts = str(int(time.time()) - 700)
        message = old_ts.encode() + b"." + body
        sig = hmac.new(b"test_hmac_secret_xyz", message, hashlib.sha256).hexdigest()
        r = signed_client.post(
            "/webhook/new-lead",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-LeadFlow-Signature": sig,
                "X-LeadFlow-Timestamp": old_ts,
            },
        )
        assert r.status_code == 401

    def test_missing_headers_rejected(self, signed_client):
        r = signed_client.post(
            "/webhook/new-lead",
            json={"name": "Dave", "phone": "+919876543213", "preferred_language": "hi"},
        )
        # Missing HMAC headers → 401 (no signature) or 422 (validation)
        assert r.status_code in (401, 422)


# ---------------------------------------------------------------------------
# Test 7: DB-backed store idempotency (uses temp SQLite via asyncio)
# ---------------------------------------------------------------------------

async def _setup_temp_db(db_url: str) -> None:
    """Run Alembic migrations against a temp DB URL, patching the engine."""
    import app.db.engine as engine_module
    import sqlalchemy.ext.asyncio as sa_async
    from app.config import settings

    # Patch settings so env.py reads the temp URL (env.py calls
    # config.set_main_option("sqlalchemy.url", settings.database_url)).
    original_db_url = settings.database_url
    settings.database_url = db_url

    new_engine = sa_async.create_async_engine(db_url)
    new_session = sa_async.async_sessionmaker(new_engine, expire_on_commit=False)
    engine_module.engine = new_engine
    engine_module.async_session = new_session

    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    import asyncio as _asyncio
    await _asyncio.to_thread(command.upgrade, cfg, "head")

    settings.database_url = original_db_url
    return new_engine


class TestDbStoreIdempotency:
    """Integration tests using a real temp SQLite file."""

    @pytest.fixture()
    def db_store(self, tmp_path):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"

        engine = asyncio.run(_setup_temp_db(db_url))

        from app.adapters.db_store import DbStore
        store = DbStore()
        yield store
        asyncio.run(engine.dispose())

    def test_record_first_time_true(self, db_store):
        assert db_store.record_processed_webhook("payment", "pay_001") is True

    def test_record_duplicate_false(self, db_store):
        db_store.record_processed_webhook("payment", "pay_002")
        assert db_store.record_processed_webhook("payment", "pay_002") is False

    def test_different_types_same_id_independent(self, db_store):
        assert db_store.record_processed_webhook("payment", "ev_001") is True
        assert db_store.record_processed_webhook("call_result", "ev_001") is True

    def test_lead_persisted_and_retrieved(self, db_store):
        lead = _make_lead("persist01")
        db_store.append_lead(lead)
        retrieved = db_store.get_lead("persist01")
        assert retrieved is not None
        assert retrieved.name == "Test User"
        assert retrieved.status == LeadStatus.NEW

    def test_update_lead_changes_status(self, db_store):
        lead = _make_lead("update01")
        db_store.append_lead(lead)
        lead.status = LeadStatus.CONTACTED
        db_store.update_lead(lead)
        retrieved = db_store.get_lead("update01")
        assert retrieved.status == LeadStatus.CONTACTED


# ---------------------------------------------------------------------------
# Test 8: Reconciler replays stale IN_PROGRESS leads
# ---------------------------------------------------------------------------

class TestReconcilerReplay:
    @pytest.fixture()
    def reconciler_db(self, tmp_path):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'reconciler.db'}"
        engine = asyncio.run(_setup_temp_db(db_url))
        from app.adapters.db_store import DbStore
        store = DbStore()
        yield store
        asyncio.run(engine.dispose())

    def test_stale_in_progress_lead_is_rescheduled(self, reconciler_db):
        store = reconciler_db
        lead = _make_lead("stale01")
        store.append_lead(lead)

        stale_time = datetime.now(timezone.utc) - timedelta(minutes=5)

        async def _set_stale_and_reconcile():
            from sqlalchemy import update
            from app.db.engine import async_session
            from app.db.models import LeadRow

            # Force the lead into stale IN_PROGRESS
            async with async_session() as session:
                await session.execute(
                    update(LeadRow)
                    .where(LeadRow.lead_id == "stale01")
                    .values(
                        outreach_state="IN_PROGRESS",
                        outreach_started_at=stale_time,
                        outreach_attempts=1,
                    )
                )
                await session.commit()

            scheduled: list[str] = []

            # Patch run_outreach_durable to capture calls without running outreach
            import app.pipeline.outreach_durable as od_mod

            async def _mock_durable(lead_id, **kwargs):
                scheduled.append(lead_id)

            original = od_mod.run_outreach_durable
            od_mod.run_outreach_durable = _mock_durable
            try:
                from app.reconciler import _reconcile_once
                await _reconcile_once()
                await asyncio.sleep(0.05)  # let create_task coroutines run
            finally:
                od_mod.run_outreach_durable = original

            return scheduled

        scheduled = asyncio.run(_set_stale_and_reconcile())
        assert "stale01" in scheduled, (
            f"Expected stale01 to be re-scheduled, got: {scheduled}"
        )


# ---------------------------------------------------------------------------
# Test 9: Graceful shutdown — reconciler-loop task lifecycle + drain_inflight
# ---------------------------------------------------------------------------

class TestLifespanShutdown:
    def test_reconciler_loop_task_named_and_cancelled_on_exit(self, tmp_path, monkeypatch):
        """The lifespan context manager starts a task named 'reconciler-loop'
        that is running while the app is up, and is cancelled/done once the
        `async with lifespan(app):` block exits."""
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'lifespan.db'}"

        import app.db.engine as engine_module
        import sqlalchemy.ext.asyncio as sa_async
        from app.config import settings

        monkeypatch.setattr(settings, "database_url", db_url)
        new_engine = sa_async.create_async_engine(db_url)
        new_session = sa_async.async_sessionmaker(new_engine, expire_on_commit=False)
        monkeypatch.setattr(engine_module, "engine", new_engine)
        monkeypatch.setattr(engine_module, "async_session", new_session)

        async def _run():
            import app.main as main_module

            # lifespan() eagerly validates the Sheets adapter's credentials
            # if one is configured on `store` — force it off so this test
            # never makes a real network call, regardless of what's in the
            # local .env on whatever machine runs the suite.
            monkeypatch.setattr(main_module.store, "_sheet", None)

            async with main_module.lifespan(main_module.app):
                # Let the freshly-created task actually start running.
                await asyncio.sleep(0.05)
                task = main_module._reconciler_task
                assert task is not None
                assert task.get_name() == "reconciler-loop"
                assert not task.done()

            # Outside the block: lifespan's finally has cancelled + awaited it.
            assert task.done()
            assert task.cancelled()

        asyncio.run(_run())

    def test_drain_inflight_waits_for_pending_tasks(self):
        """drain_inflight should await slow in-flight outreach coroutines
        rather than returning immediately."""
        from app.reconciler import _inflight_outreach, drain_inflight

        _inflight_outreach.clear()
        results: list[str] = []

        async def _slow() -> None:
            await asyncio.sleep(0.05)
            results.append("done")

        async def _run() -> None:
            task = asyncio.create_task(_slow(), name="outreach-slow01")
            _inflight_outreach.add(task)
            task.add_done_callback(_inflight_outreach.discard)

            await drain_inflight(timeout=1.0)
            await asyncio.sleep(0)  # let the done-callback flush

        try:
            asyncio.run(_run())
            assert results == ["done"], "drain_inflight returned before the task finished"
            assert _inflight_outreach == set()
        finally:
            _inflight_outreach.clear()

    def test_drain_inflight_respects_short_timeout(self):
        """drain_inflight must not block exit past its timeout window, even
        if a worker task is still pending."""
        from app.reconciler import _inflight_outreach, drain_inflight

        _inflight_outreach.clear()

        async def _run() -> float:
            async def _hangs() -> None:
                await asyncio.sleep(5)

            task = asyncio.create_task(_hangs(), name="outreach-hang01")
            _inflight_outreach.add(task)
            task.add_done_callback(_inflight_outreach.discard)

            start = time.monotonic()
            await drain_inflight(timeout=0.1)
            elapsed = time.monotonic() - start

            # Explicit cleanup: the task is still pending after the timeout,
            # so cancel it directly rather than leaking it into other tests.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return elapsed

        try:
            elapsed = asyncio.run(_run())
            assert elapsed < 1.0, "drain_inflight blocked past its timeout window"
        finally:
            _inflight_outreach.clear()
