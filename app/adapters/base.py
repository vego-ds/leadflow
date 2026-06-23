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

from app.models import CallResult, Language


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
