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

from fastapi import FastAPI
from pydantic import BaseModel

from app.adapters.dialer import MockDialer
from app.adapters.sheets import InMemorySheet
from app.adapters.whatsapp import MockEmail, MockWhatsApp
from app.pipeline.ingest import validate_and_normalize
from app.pipeline.outreach import run_outreach
from app.pipeline.scoring import score_lead

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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook/new-lead")
def new_lead(payload: NewLead):
    """Ingest -> outreach -> score. Returns the lead's state."""
    lead = validate_and_normalize(payload.model_dump(), store)
    if lead is None:
        return {"accepted": False, "reason": "quarantined - see Needs Review"}

    run_outreach(lead, store, dialer, email, whatsapp)
    score_lead(lead, store)
    return {"accepted": True, "lead_id": lead.lead_id, "status": lead.status.value,
            "score": lead.score}
