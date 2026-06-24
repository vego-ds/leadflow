"""
Outreach per-channel resilience tests.

Each channel (email, WhatsApp, call) must succeed or fail independently — a
failure in one must not block, skip, or fail the others. Uses InMemorySheet
and small fake adapters (not the real Mock* ones, which never fail) so
failures can be injected deterministically. No DB, no network.

Run: pytest -q tests/test_outreach.py
"""
from __future__ import annotations

import asyncio

import pytest

from app.adapters.base import CallOutcome, Dialer, Messenger
from app.adapters.sheets import InMemorySheet
from app.models import CallResult, Language, Lead, LeadStatus, Source
from app.pipeline.outreach import run_outreach
from app.pipeline.outreach_durable import run_outreach_durable


# ---------------------------------------------------------------------------
# _is_permanent_error: classification only — not wired into run_outreach yet
# ---------------------------------------------------------------------------

def test_error_classification():
    """_is_permanent_error correctly classifies common exceptions."""
    from app.pipeline.outreach import _is_permanent_error

    assert _is_permanent_error(ValueError("bad format")) == True
    assert _is_permanent_error(TypeError("wrong type")) == True
    assert _is_permanent_error(TimeoutError("slow")) == False
    assert _is_permanent_error(ConnectionError("network")) == False
    assert _is_permanent_error(RuntimeError("unknown")) == False  # Default to transient

# ---------------------------------------------------------------------------
# Helpers and fakes
# ---------------------------------------------------------------------------


def _make_lead(lead_id: str = "lead01") -> Lead:
    return Lead(
        lead_id=lead_id,
        name="Test User",
        phone="+919876543210",
        preferred_language=Language.HINDI,
        source=Source.WEBSITE,
        email="test@example.com",
        status=LeadStatus.NEW,
    )


def _events_for(store: InMemorySheet, lead_id: str, event_type: str) -> list[dict]:
    return [
        e for e in store.events
        if e["lead_id"] == lead_id and e["event_type"] == event_type
    ]


def _event_types_for(store: InMemorySheet, lead_id: str) -> list[str]:
    """Ordered event_type sequence for one lead, as logged."""
    return [e["event_type"] for e in store.events if e["lead_id"] == lead_id]


class _FakeMessenger(Messenger):
    """Succeeds normally; raises `error` on send() if given one."""

    def __init__(self, error: Exception | None = None):
        self.error = error

    def send(self, to: str, language: Language, attachments: list[str]) -> bool:
        if self.error is not None:
            raise self.error
        return True


class _FakeDialer(Dialer):
    """Answers normally; raises `error` on call() if given one."""

    def __init__(self, error: Exception | None = None):
        self.error = error

    def call(self, phone: str, language: Language, lead_name: str) -> CallOutcome:
        if self.error is not None:
            raise self.error
        return CallOutcome(result=CallResult.ANSWERED, needs_captured="wants weekend batch")


# ---------------------------------------------------------------------------
# run_outreach: per-channel isolation
# ---------------------------------------------------------------------------

def test_all_channels_succeeding_reports_no_failures():
    store = InMemorySheet()
    lead = _make_lead()

    result = run_outreach(lead, store, _FakeDialer(), _FakeMessenger(), _FakeMessenger())

    assert result.channel_failures == set()
    assert result.lead.call_result == CallResult.ANSWERED


def test_email_failure_does_not_block_whatsapp_or_call():
    store = InMemorySheet()
    lead = _make_lead()

    result = run_outreach(
        lead, store, _FakeDialer(), _FakeMessenger(error=RuntimeError("smtp down")), _FakeMessenger()
    )

    assert result.channel_failures == {"email"}
    assert result.lead.call_result == CallResult.ANSWERED  # call still happened
    failed = _events_for(store, lead.lead_id, "email_failed")
    assert len(failed) == 1
    assert "smtp down" in failed[0]["details"]
    assert _events_for(store, lead.lead_id, "whatsapp_sent")


def test_whatsapp_failure_does_not_block_email_or_call():
    store = InMemorySheet()
    lead = _make_lead()

    result = run_outreach(
        lead, store, _FakeDialer(), _FakeMessenger(), _FakeMessenger(error=RuntimeError("wa api down"))
    )

    assert result.channel_failures == {"whatsapp"}
    assert result.lead.call_result == CallResult.ANSWERED
    failed = _events_for(store, lead.lead_id, "whatsapp_failed")
    assert len(failed) == 1
    assert "wa api down" in failed[0]["details"]
    assert _events_for(store, lead.lead_id, "email_sent")


def test_call_failure_clears_result_without_fabricating_outcome():
    store = InMemorySheet()
    lead = _make_lead()

    result = run_outreach(
        lead, store, _FakeDialer(error=RuntimeError("dialer down")), _FakeMessenger(), _FakeMessenger()
    )

    assert result.channel_failures == {"call"}
    assert result.lead.call_result is None
    assert result.lead.needs_captured == ""
    failed = _events_for(store, lead.lead_id, "call_failed")
    assert len(failed) == 1
    assert "dialer down" in failed[0]["details"]
    assert _events_for(store, lead.lead_id, "email_sent")
    assert _events_for(store, lead.lead_id, "whatsapp_sent")


def test_all_channels_failing_reports_all_three():
    store = InMemorySheet()
    lead = _make_lead()

    result = run_outreach(
        lead, store,
        _FakeDialer(error=RuntimeError("dialer down")),
        _FakeMessenger(error=RuntimeError("smtp down")),
        _FakeMessenger(error=RuntimeError("wa api down")),
    )

    assert result.channel_failures == {"email", "whatsapp", "call"}
    assert result.channel_successes == set()
    assert result.lead.call_result is None


def test_no_email_lead_with_whatsapp_and_call_failing_has_no_successes():
    """Email is skipped (no address), not failed — it must land in neither
    set, while WhatsApp and call both genuinely failing leaves zero
    successes overall."""
    store = InMemorySheet()
    lead = _make_lead()
    lead.email = None

    result = run_outreach(
        lead, store,
        _FakeDialer(error=RuntimeError("dialer down")),
        _FakeMessenger(),
        _FakeMessenger(error=RuntimeError("wa api down")),
    )

    assert result.channel_successes == set()
    assert result.channel_failures == {"whatsapp", "call"}


def test_no_email_lead_with_whatsapp_succeeding_has_one_success():
    store = InMemorySheet()
    lead = _make_lead()
    lead.email = None

    result = run_outreach(
        lead, store,
        _FakeDialer(error=RuntimeError("dialer down")),
        _FakeMessenger(),
        _FakeMessenger(),
    )

    assert result.channel_successes == {"whatsapp"}
    assert result.channel_failures == {"call"}


def test_cancelled_error_in_email_channel_propagates():
    store = InMemorySheet()
    lead = _make_lead()

    with pytest.raises(asyncio.CancelledError):
        run_outreach(lead, store, _FakeDialer(), _FakeMessenger(error=asyncio.CancelledError()), _FakeMessenger())


def test_cancelled_error_in_whatsapp_channel_propagates():
    store = InMemorySheet()
    lead = _make_lead()

    with pytest.raises(asyncio.CancelledError):
        run_outreach(lead, store, _FakeDialer(), _FakeMessenger(), _FakeMessenger(error=asyncio.CancelledError()))


def test_cancelled_error_in_call_channel_propagates():
    store = InMemorySheet()
    lead = _make_lead()

    with pytest.raises(asyncio.CancelledError):
        run_outreach(lead, store, _FakeDialer(error=asyncio.CancelledError()), _FakeMessenger(), _FakeMessenger())


# ---------------------------------------------------------------------------
# run_outreach: permanent_error classification, wired into the except blocks
# ---------------------------------------------------------------------------

def test_transient_error_leaves_permanent_error_false():
    store = InMemorySheet()
    lead = _make_lead()

    result = run_outreach(
        lead, store, _FakeDialer(), _FakeMessenger(error=TimeoutError("connection slow")), _FakeMessenger()
    )

    assert result.permanent_error == False
    failed = _events_for(store, lead.lead_id, "email_failed")
    assert "[transient]" in failed[0]["details"]


def test_permanent_error_sets_permanent_error_true():
    store = InMemorySheet()
    lead = _make_lead()

    result = run_outreach(
        lead, store, _FakeDialer(), _FakeMessenger(), _FakeMessenger(error=ValueError("invalid phone"))
    )

    assert result.permanent_error == True
    failed = _events_for(store, lead.lead_id, "whatsapp_failed")
    assert "[permanent]" in failed[0]["details"]


def test_one_permanent_error_takes_precedence_over_other_successes():
    store = InMemorySheet()
    lead = _make_lead()

    result = run_outreach(
        lead, store, _FakeDialer(), _FakeMessenger(error=ValueError("invalid address")), _FakeMessenger()
    )

    assert result.permanent_error == True
    assert result.channel_successes == {"whatsapp", "call"}


# ---------------------------------------------------------------------------
# run_outreach_durable: outreach_state contract (no DB — claim/mark stubbed)
# ---------------------------------------------------------------------------

@pytest.fixture()
def _stub_db_layer(monkeypatch):
    """No DB, no network: stand in for the claim/mark helpers that bridge to
    SQLAlchemy, and record what run_outreach_durable decided to do."""
    import app.pipeline.outreach_durable as od

    calls = {"done": [], "failed_or_retry": []}

    async def _claim_lead(lead_id, now):
        return True

    async def _mark_done(lead_id):
        calls["done"].append(lead_id)

    async def _mark_failed_or_retry(lead_id):
        calls["failed_or_retry"].append(lead_id)
        return 1

    monkeypatch.setattr(od, "_claim_lead", _claim_lead)
    monkeypatch.setattr(od, "_mark_done", _mark_done)
    monkeypatch.setattr(od, "_mark_failed_or_retry", _mark_failed_or_retry)
    return calls


def _store_with_lead_and_counselor(lead: Lead) -> InMemorySheet:
    store = InMemorySheet()
    store.seed_counselors([{
        "name": "Anita", "languages_spoken": ["hi", "en"],
        "available_slots": ["Mon 10"], "current_load": 0,
    }])
    store.append_lead(lead)
    return store


def test_durable_outreach_marks_done_when_all_channels_succeed(_stub_db_layer):
    lead = _make_lead("durable01")
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(), email=_FakeMessenger(), whatsapp=_FakeMessenger()
    ))

    assert _stub_db_layer["done"] == [lead.lead_id]
    assert _stub_db_layer["failed_or_retry"] == []
    updated = store.get_lead(lead.lead_id)
    assert updated.status == LeadStatus.ASSIGNED


def test_durable_outreach_marks_done_when_only_email_fails(_stub_db_layer):
    lead = _make_lead("durable02")
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(),
        email=_FakeMessenger(error=RuntimeError("smtp down")), whatsapp=_FakeMessenger(),
    ))

    assert _stub_db_layer["done"] == [lead.lead_id]
    assert _stub_db_layer["failed_or_retry"] == []
    assert _events_for(store, lead.lead_id, "email_failed")
    updated = store.get_lead(lead.lead_id)
    assert updated.call_result == CallResult.ANSWERED  # lead still proceeded to screening


def test_durable_outreach_marks_done_when_only_whatsapp_fails(_stub_db_layer):
    lead = _make_lead("durable03")
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(), email=_FakeMessenger(),
        whatsapp=_FakeMessenger(error=RuntimeError("wa api down")),
    ))

    assert _stub_db_layer["done"] == [lead.lead_id]
    assert _stub_db_layer["failed_or_retry"] == []
    assert _events_for(store, lead.lead_id, "whatsapp_failed")
    updated = store.get_lead(lead.lead_id)
    assert updated.call_result == CallResult.ANSWERED


def test_durable_outreach_marks_done_when_only_call_fails(_stub_db_layer):
    lead = _make_lead("durable04")
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(error=RuntimeError("dialer down")),
        email=_FakeMessenger(), whatsapp=_FakeMessenger(),
    ))

    assert _stub_db_layer["done"] == [lead.lead_id]
    assert _stub_db_layer["failed_or_retry"] == []
    updated = store.get_lead(lead.lead_id)
    # Call failed -> no screening outcome -> handle_call_result never ran.
    assert updated.call_result is None
    assert updated.needs_captured == ""
    assert updated.status == LeadStatus.NEW


def test_durable_outreach_retries_when_all_channels_fail(_stub_db_layer):
    lead = _make_lead("durable05")
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store,
        dialer=_FakeDialer(error=RuntimeError("dialer down")),
        email=_FakeMessenger(error=RuntimeError("smtp down")),
        whatsapp=_FakeMessenger(error=RuntimeError("wa api down")),
    ))

    assert _stub_db_layer["failed_or_retry"] == [lead.lead_id]
    assert _stub_db_layer["done"] == []


def test_durable_outreach_retries_when_no_email_and_remaining_channels_fail(_stub_db_layer):
    """A no-email lead can never accumulate 3 channel_failures (email is
    skipped, not failed) — the retry decision must key off zero successes,
    not a literal {"email", "whatsapp", "call"} failure set."""
    lead = _make_lead("durable06")
    lead.email = None
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store,
        dialer=_FakeDialer(error=RuntimeError("dialer down")),
        email=_FakeMessenger(),
        whatsapp=_FakeMessenger(error=RuntimeError("wa api down")),
    ))

    assert _stub_db_layer["failed_or_retry"] == [lead.lead_id]
    assert _stub_db_layer["done"] == []


def test_durable_outreach_marks_done_when_no_email_but_whatsapp_succeeds(_stub_db_layer):
    lead = _make_lead("durable07")
    lead.email = None
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store,
        dialer=_FakeDialer(error=RuntimeError("dialer down")),
        email=_FakeMessenger(),
        whatsapp=_FakeMessenger(),
    ))

    assert _stub_db_layer["done"] == [lead.lead_id]
    assert _stub_db_layer["failed_or_retry"] == []


# ---------------------------------------------------------------------------
# Step-level logging: attempt boundaries + consistent call_sent/call_failed
# ---------------------------------------------------------------------------

def test_email_failure_logs_exception_type_and_message():
    """call_failed/email_failed/whatsapp_failed details include the
    exception type, not just the message — more useful in the Events tab."""
    store = InMemorySheet()
    lead = _make_lead()

    run_outreach(lead, store, _FakeDialer(), _FakeMessenger(error=ValueError("invalid address")), _FakeMessenger())

    failed = _events_for(store, lead.lead_id, "email_failed")
    assert len(failed) == 1
    assert failed[0]["details"] == "ValueError: invalid address [permanent]"


def test_durable_outreach_logs_full_lifecycle_when_all_channels_succeed(_stub_db_layer):
    lead = _make_lead("lifecycle01")
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(), email=_FakeMessenger(), whatsapp=_FakeMessenger()
    ))

    expected = [
        "outreach_attempt_started", "email_sent", "whatsapp_sent", "call_sent",
        "outreach_attempt_completed", "outreach_complete",
    ]
    # Filter to just the named steps — scored/counselor_assigned etc. land
    # in between (from handle_call_result) and aren't part of this contract.
    actual = [t for t in _event_types_for(store, lead.lead_id) if t in expected]
    assert actual == expected


def test_durable_outreach_logs_retry_scheduled_when_no_email_and_remaining_fail(_stub_db_layer):
    lead = _make_lead("lifecycle02")
    lead.email = None
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store,
        dialer=_FakeDialer(error=RuntimeError("dialer down")),
        email=_FakeMessenger(),
        whatsapp=_FakeMessenger(error=RuntimeError("wa api down")),
    ))

    expected = [
        "outreach_attempt_started", "whatsapp_failed", "call_failed",
        "outreach_attempt_completed", "outreach_retry_scheduled",
    ]
    actual = [t for t in _event_types_for(store, lead.lead_id) if t in expected]
    assert actual == expected


def test_durable_outreach_quarantines_on_permanent_error(_stub_db_layer):
    """A permanent error on any channel quarantines the lead instead of
    retrying — bad data won't fix itself on a reconciler retry."""
    lead = _make_lead("quarantine01")
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(), whatsapp=_FakeMessenger(),
        email=_FakeMessenger(error=ValueError("invalid address")),
    ))

    assert _stub_db_layer["failed_or_retry"] == []
    assert _stub_db_layer["done"] == []
    assert len(store.needs_review) == 1
    quarantined = store.needs_review[0]
    assert quarantined["lead_id"] == lead.lead_id
    assert quarantined["validation_error"] == "Outreach failed with permanent error: email"
    assert _events_for(store, lead.lead_id, "outreach_quarantined")


def test_durable_outreach_transient_error_still_retries_not_quarantines(_stub_db_layer):
    """A transient error never quarantines — it goes through the existing
    zero-successes retry path."""
    lead = _make_lead("quarantine02")
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store,
        dialer=_FakeDialer(error=RuntimeError("dialer down")),
        email=_FakeMessenger(error=RuntimeError("smtp down")),
        whatsapp=_FakeMessenger(error=RuntimeError("wa api down")),
    ))

    assert _stub_db_layer["failed_or_retry"] == [lead.lead_id]
    assert _stub_db_layer["done"] == []
    assert store.needs_review == []
    assert not _events_for(store, lead.lead_id, "outreach_quarantined")


def test_durable_outreach_attempt_completed_event_reflects_partial_success(_stub_db_layer):
    lead = _make_lead("lifecycle03")
    store = _store_with_lead_and_counselor(lead)

    asyncio.run(run_outreach_durable(
        lead.lead_id, store=store, dialer=_FakeDialer(),
        email=_FakeMessenger(), whatsapp=_FakeMessenger(error=RuntimeError("wa api down")),
    ))

    completed = _events_for(store, lead.lead_id, "outreach_attempt_completed")
    assert len(completed) == 1
    details = completed[0]["details"]
    assert "channel_successes=['call', 'email']" in details
    assert "channel_failures=['whatsapp']" in details
