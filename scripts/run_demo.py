"""
LeadFlow demo runner.

Wires the mock adapters and runs sample leads end-to-end through the pipeline:
ingest -> outreach -> scoring -> booking -> (human zone) -> conversion.

Run:  python -m scripts.run_demo
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from app.adapters.dialer import MockDialer
from app.adapters.sheets import InMemorySheet
from app.adapters.whatsapp import MockEmail, MockWhatsApp
from app.models import LeadStatus
from app.pipeline.conversion import handle_payment, mark_lost
from app.pipeline.ingest import validate_and_normalize
from app.pipeline.outreach import handle_call_result, run_outreach
from app.utils.logging import log

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SAMPLE = DATA_DIR / "sample_leads.csv"
COUNSELORS_FILE = DATA_DIR / "counselors.json"


def main():
    store = InMemorySheet()
    dialer, email, whatsapp = MockDialer(), MockEmail(), MockWhatsApp()

    with open(COUNSELORS_FILE, encoding="utf-8") as f:
        store.seed_counselors(json.load(f))

    with open(SAMPLE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    log(f"Ingesting {len(rows)} rows...\n")

    for row in rows:
        lead = validate_and_normalize(row, store)
        if lead is None:
            log(f"[QUARANTINED] {row.get('name', '?')}")
            continue

        outcome = run_outreach(lead, store, dialer, email, whatsapp)
        handle_call_result(lead, outcome.result, outcome.needs_captured, store)

        # --- Human zone is simulated below for the demo only ---
        # Two leads convert, one is lost; the rest stop at Assigned.
        if lead.score and lead.score >= 85:
            lead.status = LeadStatus.IN_DISCUSSION
            store.update_lead(lead)
            handle_payment(lead, payment_id=f"pay_{lead.lead_id}", store=store)
        elif lead.call_result and lead.call_result.value == "no_answer" and lead.score and lead.score < 40:
            mark_lost(lead, "unreachable", store)

        log(
            f"  {lead.name:<16} {lead.preferred_language.value}  "
            f"score={lead.score:<3} call={lead.call_result.value if lead.call_result else '-':<10} "
            f"-> {lead.status.value}  ({lead.assigned_counselor or 'unassigned'})"
        )

    _summary(store)


def _summary(store: InMemorySheet):
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    counts: dict[str, int] = {}
    for row in store.leads.values():
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    for status, n in sorted(counts.items()):
        print(f"  {status:<16} {n}")
    print(f"\n  Quarantined (Needs Review): {len(store.needs_review)}")
    print(f"  Events logged:              {len(store.events)}")


if __name__ == "__main__":
    main()
