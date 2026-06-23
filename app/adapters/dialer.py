"""
Voice dialer adapters.

MockDialer  - simulates a multilingual screening call. No telephony, no cost.
BolnaDialer - real integration stub. Swap this in with credentials to go live.
"""
from __future__ import annotations

import random
import time

from app.adapters.base import CallOutcome, Dialer
from app.models import CallResult, Language

# Plausible screening outcomes when a lead actually answers.
_SAMPLE_NEEDS = [
    "Wants weekend batch, working professional",
    "Asking about fees and EMI options",
    "Interested in data science track, beginner",
    "Comparing us with two other platforms",
    "Needs placement assistance details",
    "Parent enquiring on behalf of student",
]


class MockDialer(Dialer):
    """Pretends to place a call. Returns a believable outcome after a short delay."""

    def __init__(self, simulate_delay: bool = True):
        self.simulate_delay = simulate_delay

    def call(self, phone: str, language: Language, lead_name: str) -> CallOutcome:
        if self.simulate_delay:
            time.sleep(random.uniform(1.0, 2.5))  # stand-in for ring + talk time

        result = random.choices(
            [CallResult.ANSWERED, CallResult.NO_ANSWER, CallResult.VOICEMAIL],
            weights=[0.55, 0.30, 0.15],
        )[0]

        needs = random.choice(_SAMPLE_NEEDS) if result == CallResult.ANSWERED else ""
        return CallOutcome(result=result, needs_captured=needs)


class BolnaDialer(Dialer):
    """
    Real Bolna AI integration. Not active in the demo.

    To go live:
      1. Set BOLNA_API_KEY and the agent_id (from bolna_agent/agent_config.json).
      2. Implement call() against the Bolna API.
      3. Register this dialer instead of MockDialer in config.
    """

    def __init__(self, api_key: str, agent_id: str):
        self.api_key = api_key
        self.agent_id = agent_id

    def call(self, phone: str, language: Language, lead_name: str) -> CallOutcome:
        raise NotImplementedError(
            "BolnaDialer is a stub. Wire up the Bolna API here to enable live calls."
        )
