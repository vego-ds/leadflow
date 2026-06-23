"""
LeadFlow - FastAPI entry point.

Exposes the webhooks the system reacts to:
  POST /webhook/new-lead     <- Google Apps Script, on new row
  POST /webhook/call-result  <- Bolna, after a screening call
  POST /webhook/payment      <- payment provider, on successful payment

In the demo these run against the in-memory store with mock adapters.
Swap InMemorySheet -> GoogleSheet and MockDialer -> BolnaDialer to go live.
"""
from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, HTTPException, Response
from pydantic import BaseModel

from app.adapters.base import SheetStore
from app.adapters.dialer import MockDialer
from app.adapters.sheets import InMemorySheet
from app.adapters.whatsapp import MockEmail, MockWhatsApp
from app.models import CallResult, Lead
from app.pipeline.conversion import handle_payment
from app.pipeline.ingest import validate_and_normalize
from app.pipeline.outreach import handle_call_result, run_outreach

app = FastAPI(title="LeadFlow", version="0.1.0")

# Demo singletons. In production these come from config / DI.
store = InMemorySheet()
dialer, email, whatsapp = MockDialer(simulate_delay=False), MockEmail(), MockWhatsApp()


class NewLead(BaseModel):
    name: str
    phone: str
    preferred_language: str
    source: str = "manual"
    email: str | None = None
    raw_notes: str = ""


class CallResultEvent(BaseModel):
    lead_id: str
    result: CallResult
    needs_captured: str = ""


class PaymentEvent(BaseModel):
    lead_id: str
    payment_id: str


@app.get("/health")
def health():
    return {"status": "ok"}


def _dispatch_outreach(lead: Lead, store: SheetStore) -> None:
    """Background-task path: fire outreach, then run the same screening
    handling a real Bolna callback would trigger via /webhook/call-result."""
    outcome = run_outreach(lead, store, dialer, email, whatsapp)
    handle_call_result(lead, outcome.result, outcome.needs_captured, store)


@app.post("/webhook/new-lead")
def new_lead(payload: NewLead, background_tasks: BackgroundTasks, response: Response):
    """Ingest synchronously, then enqueue outreach so the response doesn't
    wait on the call/email/WhatsApp dispatch."""
    lead = validate_and_normalize(payload.model_dump(), store)
    if lead is None:
        return {"accepted": False, "reason": "quarantined - see Needs Review"}

    background_tasks.add_task(_dispatch_outreach, lead, store)
    response.status_code = 202
    return {"lead_id": lead.lead_id, "status": lead.status.value}


@app.post("/webhook/call-result")
def call_result(payload: CallResultEvent):
    """Bolna's screening callback. Shares handle_call_result() with the
    background-task path that runs after the mock dialer's call."""
    lead = store.get_lead(payload.lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="unknown lead_id")

    handle_call_result(lead, payload.result, payload.needs_captured, store)
    return {"lead_id": lead.lead_id, "status": lead.status.value, "score": lead.score,
            "assigned_counselor": lead.assigned_counselor}


@app.post("/webhook/payment")
def payment(payload: PaymentEvent):
    """Payment provider notifies us a lead has paid. Idempotent per payment_id."""
    lead = store.get_lead(payload.lead_id)
    if lead is None:
        return {"accepted": False, "reason": "unknown lead_id"}

    handle_payment(lead, payload.payment_id, store)
    return {"accepted": True, "lead_id": lead.lead_id, "status": lead.status.value}
