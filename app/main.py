"""
LeadFlow - FastAPI entry point.

Exposes the webhooks the system reacts to:
  POST /webhook/new-lead     <- Google Apps Script, on new row (HMAC-signed)
  POST /webhook/call-result  <- Bolna, after a screening call (HMAC-signed)
  POST /webhook/payment      <- payment provider, on successful payment

SYSTEM OF RECORD: SQLite via SQLAlchemy async (app/db/). Sheets are a
human-facing projection only — they are not queried for pipeline decisions.

In the demo these run against the in-memory store with mock adapters.
Swap InMemorySheet -> DbStore and MockDialer -> BolnaDialer to go live.

HMAC signing:
  Both /webhook/new-lead and /webhook/call-result require a valid
  X-LeadFlow-Signature and X-LeadFlow-Timestamp header. When WEBHOOK_SIGNING_SECRET
  (or BOLNA_WEBHOOK_SECRET) is empty the dependency is skipped so the demo
  and tests work without credentials.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from app.adapters.base import SheetStore
from app.adapters.db_store import DbStore
from app.adapters.dialer import MockDialer
from app.adapters.sheets import InMemorySheet
from app.adapters.whatsapp import MockEmail, MockWhatsApp
from app.config import settings
from app.db.engine import dispose_engine
from app.models import CallResult, Lead, LeadStatus
from app.pipeline.conversion import handle_payment
from app.pipeline.ingest import validate_and_normalize
from app.pipeline.outreach import run_outreach
from app.pipeline.outreach_durable import run_outreach_durable
from app.pipeline.transitions import IllegalTransition, set_status
from app.reconciler import drain_inflight, reconcile_outreach
from app.security.webhook_auth import require_signed_webhook

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

_reconciler_task: asyncio.Task | None = None


def _run_pending_migrations() -> None:
    """Sync Alembic call - migrations/env.py drives its own asyncio.run(),
    so this must execute off the running event loop (see lifespan below)."""
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _reconciler_task
    await asyncio.to_thread(_run_pending_migrations)
    _reconciler_task = asyncio.create_task(reconcile_outreach(), name="reconciler-loop")
    try:
        yield
    finally:
        # Resource-freeing order: stop new work, drain in-flight work,
        # then tear down the engine everything else depends on.
        if _reconciler_task is not None:
            _reconciler_task.cancel()
            await asyncio.gather(_reconciler_task, return_exceptions=True)
        await drain_inflight(timeout=10.0)
        await dispose_engine()


app = FastAPI(title="LeadFlow", version="0.1.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Adapters — mock for demo; swap to real implementations for production.
# DbStore is used as the default store. InMemorySheet stays for unit tests.
# ---------------------------------------------------------------------------
store: SheetStore = InMemorySheet()   # overridden in production via startup
dialer = MockDialer(simulate_delay=False)
email = MockEmail()
whatsapp = MockWhatsApp()

# ---------------------------------------------------------------------------
# Signature dependencies — skipped when secret is empty (demo / CI).
# ---------------------------------------------------------------------------

def _new_lead_auth_dep():
    """Returns the HMAC dependency if a signing secret is configured."""
    if settings.webhook_signing_secret:
        return Depends(require_signed_webhook(settings.webhook_signing_secret))
    return None


def _call_result_auth_dep():
    """Returns the HMAC dependency if Bolna signing secret is configured."""
    if settings.bolna_webhook_secret:
        return Depends(require_signed_webhook(settings.bolna_webhook_secret))
    return None


# ---------------------------------------------------------------------------
# Pydantic request bodies
# ---------------------------------------------------------------------------

class NewLead(BaseModel):
    name: str
    phone: str
    preferred_language: str
    source: str = "manual"
    email: str | None = None
    raw_notes: str = ""


class CallResultEvent(BaseModel):
    lead_id: str
    call_id: str
    result: CallResult
    needs_captured: str = ""


class PaymentEvent(BaseModel):
    lead_id: str
    payment_id: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook/new-lead")
async def new_lead(
    request: Request,
    payload: NewLead,
    background_tasks: BackgroundTasks,
    response: Response,
):
    """Ingest synchronously, then schedule durable outreach.

    HMAC signature verified via X-LeadFlow-Signature / X-LeadFlow-Timestamp
    when WEBHOOK_SIGNING_SECRET is configured.
    """
    # Inline HMAC check (avoids dependency injection complexity with optional auth)
    if settings.webhook_signing_secret:
        sig = request.headers.get("X-LeadFlow-Signature", "")
        ts = request.headers.get("X-LeadFlow-Timestamp", "")
        body = await request.body()
        from app.security.webhook_auth import verify_signature
        verify_signature(body, sig, ts, settings.webhook_signing_secret)

    lead = validate_and_normalize(payload.model_dump(), store)
    if lead is None:
        return {"accepted": False, "reason": "quarantined - see Needs Review"}

    background_tasks.add_task(
        _run_outreach_in_background, lead.lead_id
    )
    response.status_code = 202
    return {"lead_id": lead.lead_id, "status": lead.status.value}


def _run_outreach_in_background(lead_id: str) -> None:
    """BackgroundTask shim: runs the async durable outreach from a thread context.

    FastAPI BackgroundTasks run in a thread pool, so there is no running event
    loop here. asyncio.run() creates a fresh loop for the duration of this call.
    """
    asyncio.run(
        run_outreach_durable(
            lead_id, store=store, dialer=dialer, email=email, whatsapp=whatsapp
        )
    )


@app.post("/webhook/call-result")
async def call_result(request: Request, payload: CallResultEvent):
    """Bolna's screening callback. Idempotent per call_id.

    TODO (Bolna signing): Once Bolna's signing scheme is confirmed against
    their docs (see bolna_agent/agent_config.json), set BOLNA_WEBHOOK_SECRET
    in .env. The verify_signature() call below uses the same HMAC-SHA256
    protocol as /webhook/new-lead — adjust if Bolna uses a different format.
    """
    # Inline HMAC check for Bolna webhook
    if settings.bolna_webhook_secret:
        sig = request.headers.get("X-LeadFlow-Signature", "")
        ts = request.headers.get("X-LeadFlow-Timestamp", "")
        body = await request.body()
        from app.security.webhook_auth import verify_signature
        verify_signature(body, sig, ts, settings.bolna_webhook_secret)

    # Idempotency: dedupe by call_id
    is_new = store.record_processed_webhook("call_result", payload.call_id)
    if not is_new:
        return {"status": "already_processed"}

    lead = store.get_lead(payload.lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="unknown lead_id")

    # State-machine guard: only CONTACTED leads can be screened.
    if lead.status != LeadStatus.CONTACTED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Lead {payload.lead_id} is in status {lead.status.value!r}, "
                f"expected 'Contacted'. Late or replayed callback rejected."
            ),
        )

    lead.call_result = payload.result
    lead.needs_captured = payload.needs_captured

    try:
        set_status(lead, LeadStatus.SCREENED, store)
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    from app.pipeline.scoring import score_lead
    from app.pipeline.booking import assign_counselor
    score_lead(lead, store)
    assign_counselor(lead, store)

    return {
        "lead_id": lead.lead_id,
        "status": lead.status.value,
        "score": lead.score,
        "assigned_counselor": lead.assigned_counselor,
    }


@app.post("/webhook/payment")
def payment(payload: PaymentEvent):
    """Payment provider notifies us a lead has paid. Idempotent per payment_id."""
    lead = store.get_lead(payload.lead_id)
    if lead is None:
        return {"accepted": False, "reason": "unknown lead_id"}

    handle_payment(lead, payload.payment_id, store)
    return {"accepted": True, "lead_id": lead.lead_id, "status": lead.status.value}
