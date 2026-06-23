"""
Stage 1 - Ingest: validate & normalize.

A row from the Sheet is human-edited and may be malformed. We enforce the
schema in code (not in the Sheet). Clean rows become Lead objects; bad rows
are quarantined to Needs Review instead of crashing the pipeline.
"""
from __future__ import annotations

import re
import uuid

from app.adapters.sheets import SheetStore
from app.models import Language, Lead, LeadStatus, Source

_PHONE_RE = re.compile(r"^\+?\d{10,13}$")


def _clean_phone(raw: str) -> str:
    return re.sub(r"[\s\-()]", "", raw or "")


def validate_and_normalize(row: dict, store: SheetStore) -> Lead | None:
    """Return a Lead if the row is valid, else quarantine it and return None."""
    name = (row.get("name") or "").strip()
    phone = _clean_phone(row.get("phone", ""))
    lang_raw = (row.get("preferred_language") or "").strip().lower()
    source_raw = (row.get("source") or "").strip().lower()

    if not name:
        store.quarantine(row, "missing name")
        return None
    if not _PHONE_RE.match(phone):
        store.quarantine(row, f"invalid phone: {row.get('phone')!r}")
        return None
    try:
        language = Language(lang_raw)
    except ValueError:
        store.quarantine(row, f"unknown language: {lang_raw!r}")
        return None
    try:
        source = Source(source_raw)
    except ValueError:
        source = Source.MANUAL  # unknown source is tolerable; default it

    lead = Lead(
        lead_id=str(uuid.uuid4())[:8],
        name=name,
        phone=phone,
        email=(row.get("email") or "").strip() or None,
        preferred_language=language,
        source=source,
        raw_notes=(row.get("raw_notes") or "").strip(),
        status=LeadStatus.NEW,
    )
    store.append_lead(lead)
    store.log_event(lead.lead_id, "lead_created", f"source={source.value}")
    return lead
