"""
LeadFlow demo runner.

Wires the mock adapters and runs sample leads end-to-end through the pipeline:
ingest -> outreach -> scoring -> booking -> (human zone) -> conversion.

Run:  python -m scripts.run_demo
"""
from __future__ import annotations

import csv
from pathlib import Path

from app.adapters.dialer import MockDialer
from app.adapters.sheets import InMemorySheet
from app.adapters.whatsapp import MockEmail, MockWhatsApp
from app.models import LeadStatus
from app.pipeline.booking import assign_counselor
from app.pipeline.conversion import handle_payment, mark_lost
from app.pipeline.ingest import validate_and_normalize
from app.pipeline.outreach import run_outreach
from app.pipeline.scoring import score_lead
from app.utils.logging import log

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_leads.csv"

# Demo counselor roster (would live in the Counselors tab).
COUNSELORS = [
    {"name": "Anita", "languages_spoken": ["hi", "en"],
     "available_slots": ["Mon 10:00", "Mon 11:00", "Tue 10:00"], "current_load": 0},
    {"name": "Ravi", "languages_spoken": ["te", "en"],
     "available_slots": ["Mon 14:00", "Tue 15:00"], "current_load": 0},
    {"name": "Meera", "languages_spoken": ["ta", "ml", "en"],
     "available_slots": ["Mon 16:00", "Wed 10:00"], "current_load": 0},
    {"name": "Kiran", "languages_spoken": ["kn", "en"],
     "available_slots": ["Tue 11:00", "Wed 14:00"], "current_load": 0},
]


def main():
    store = InMemorySheet()
    dialer, email, whatsapp = MockDialer(), MockEmail(), MockWhatsApp()

    with open(SAMPLE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    log(f"Ingesting {len(rows)} rows...\n")

    for row in rows:
        lead = validate_and_normalize(row, store)
        if lead is None:
            log(f"[QUARANTINED] {row.get('name', '?')}")
            continue

        run_outreach(lead, store, dialer, email, whatsapp)
        score_lead(lead, store)
        assign_counselor(lead, COUNSELORS, store)

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
