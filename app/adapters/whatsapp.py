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


class SmtpEmail(Messenger):
    """
    Real SMTP email integration. Not active in the demo.

    To go live:
      1. Set SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_ADDR.
      2. Implement send() against smtplib.
      3. Register this messenger instead of MockEmail in config.
    """

    def __init__(self, host: str, port: int, username: str, password: str, from_addr: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_addr = from_addr

    def send(self, to: str, language: Language, attachments: list[str]) -> bool:
        raise NotImplementedError("SmtpEmail is a stub. Wire up smtplib here.")


class WhatsAppBusinessAPI(Messenger):
    """
    Real WhatsApp Business API integration. Not active in the demo.

    To go live:
      1. Set WHATSAPP_API_TOKEN and WHATSAPP_PHONE_NUMBER_ID.
      2. Implement send() against the WhatsApp Business API.
      3. Register this messenger instead of MockWhatsApp in config.
    """

    def __init__(self, api_token: str, phone_number_id: str):
        self.api_token = api_token
        self.phone_number_id = phone_number_id

    def send(self, to: str, language: Language, attachments: list[str]) -> bool:
        raise NotImplementedError("WhatsAppBusinessAPI is a stub. Wire up the WhatsApp Business API here.")
