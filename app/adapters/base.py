"""
Adapter interfaces.

Every external action (call, email, whatsapp, payment, sheet) goes through
one of these interfaces. The demo uses Mock* implementations; going live means
swapping in the real one (e.g. MockDialer -> BolnaDialer) with no change to
the pipeline code that calls them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models import CallResult, Language, Lead


@dataclass
class CallOutcome:
    result: CallResult
    needs_captured: str = ""


class Dialer(ABC):
    @abstractmethod
    def call(self, phone: str, language: Language, lead_name: str) -> CallOutcome:
        """Place the screening call. Returns the outcome."""


class Messenger(ABC):
    @abstractmethod
    def send(self, to: str, language: Language, attachments: list[str]) -> bool:
        """Send a message (email or whatsapp) with attachments. Returns success."""


class SheetStore(ABC):
    @abstractmethod
    def append_lead(self, lead: Lead) -> None: ...

    @abstractmethod
    def update_lead(self, lead: Lead) -> None: ...

    @abstractmethod
    def get_lead(self, lead_id: str) -> Lead | None: ...

    @abstractmethod
    def quarantine(self, row: dict, error: str) -> None: ...

    @abstractmethod
    def log_event(self, lead_id: str, event_type: str, details: str = "") -> None: ...

    @abstractmethod
    def get_counselors(self) -> list[dict]: ...

    @abstractmethod
    def update_counselor(self, counselor: dict) -> None: ...
