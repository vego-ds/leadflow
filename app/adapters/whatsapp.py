"""
Email and WhatsApp adapters.

Both are mocked for the demo: they log the send instead of hitting a real
provider. Swap in real implementations (SMTP/SendGrid, WhatsApp Business API)
behind the same Messenger interface to go live.
"""
from __future__ import annotations

from app.adapters.base import Messenger
from app.models import Language
from app.utils.logging import log


class MockEmail(Messenger):
    def send(self, to: str, language: Language, attachments: list[str]) -> bool:
        if not to:
            return False
        log(f"[EMAIL] to={to} lang={language.value} attachments={attachments}")
        return True


class MockWhatsApp(Messenger):
    def send(self, to: str, language: Language, attachments: list[str]) -> bool:
        if not to:
            return False
        log(f"[WHATSAPP] to={to} lang={language.value} attachments={attachments}")
        return True
