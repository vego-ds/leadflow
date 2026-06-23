"""Core data models for LeadFlow."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from app.utils.time import now_utc_iso


class LeadStatus(str, Enum):
    """The lead lifecycle. Automated front, human-executed back."""
    NEW = "New"
    CONTACTED = "Contacted"        # call/email/whatsapp fired
    SCREENED = "Screened"          # screening outcome captured
    ASSIGNED = "Assigned"          # counselor + slot booked
    IN_DISCUSSION = "InDiscussion"  # human zone begins
    PAYMENT_LINK_SENT = "PaymentLinkSent"
    CONVERTED = "Converted"         # payment received, pipeline ends
    LOST = "Lost"                   # can branch off any stage


class Source(str, Enum):
    META_AD = "meta_ad"
    WEBSITE = "website"
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    MANUAL = "manual"


class Language(str, Enum):
    ENGLISH = "en"
    HINDI = "hi"
    TELUGU = "te"
    KANNADA = "kn"
    TAMIL = "ta"
    MALAYALAM = "ml"


class CallResult(str, Enum):
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"


@dataclass
class Lead:
    lead_id: str
    name: str
    phone: str
    preferred_language: Language
    source: Source
    email: Optional[str] = None
    raw_notes: str = ""
    status: LeadStatus = LeadStatus.NEW
    score: Optional[int] = None
    assigned_counselor: Optional[str] = None
    counselor_slot: Optional[str] = None
    call_result: Optional[CallResult] = None
    needs_captured: str = ""
    timestamp_created: str = field(default_factory=now_utc_iso)
    last_updated: str = field(default_factory=now_utc_iso)

    def to_row(self) -> dict:
        """Flatten to a Sheet row (enums -> their string values)."""
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, Enum):
                d[k] = v.value
        return d
