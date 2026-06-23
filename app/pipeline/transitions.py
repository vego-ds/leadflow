"""
Lead status state machine.

LEGAL_TRANSITIONS defines all valid (current_status -> next_status) moves.
Any attempt to move outside these edges raises IllegalTransition, which lets
webhook handlers safely reject replayed or out-of-order events.

Usage:
    from app.pipeline.transitions import set_status, IllegalTransition

    set_status(lead, LeadStatus.SCREENED, store)  # raises if illegal
"""
from __future__ import annotations

from app.adapters.base import SheetStore
from app.models import Lead, LeadStatus

# Maps current status -> set of allowed next statuses.
# LOST is a terminal absorbing state reachable from any stage.
LEGAL_TRANSITIONS: dict[LeadStatus, set[LeadStatus]] = {
    LeadStatus.NEW: {LeadStatus.CONTACTED, LeadStatus.LOST},
    LeadStatus.CONTACTED: {LeadStatus.SCREENED, LeadStatus.LOST},
    LeadStatus.SCREENED: {LeadStatus.ASSIGNED, LeadStatus.LOST},
    LeadStatus.ASSIGNED: {LeadStatus.IN_DISCUSSION, LeadStatus.LOST},
    LeadStatus.IN_DISCUSSION: {LeadStatus.PAYMENT_LINK_SENT, LeadStatus.LOST},
    LeadStatus.PAYMENT_LINK_SENT: {LeadStatus.CONVERTED, LeadStatus.LOST},
    LeadStatus.CONVERTED: set(),   # terminal
    LeadStatus.LOST: set(),        # terminal
}


class IllegalTransition(Exception):
    """Raised when a status transition is not permitted by the state machine."""

    def __init__(self, current: LeadStatus, next_status: LeadStatus) -> None:
        super().__init__(
            f"Illegal transition: {current.value!r} -> {next_status.value!r}. "
            f"Allowed: {[s.value for s in LEGAL_TRANSITIONS.get(current, set())]}"
        )
        self.current = current
        self.next_status = next_status


def assert_transition(current: LeadStatus, next_status: LeadStatus) -> None:
    """Raise IllegalTransition if the move is not in LEGAL_TRANSITIONS."""
    allowed = LEGAL_TRANSITIONS.get(current, set())
    if next_status not in allowed:
        raise IllegalTransition(current, next_status)


def set_status(lead: Lead, next_status: LeadStatus, store: SheetStore) -> None:
    """Transition lead.status to next_status, enforcing the state machine.

    Raises IllegalTransition if the move is not permitted.
    Persists the lead and logs a status_changed event.
    """
    assert_transition(lead.status, next_status)
    lead.status = next_status
    store.update_lead(lead)
    store.log_event(lead.lead_id, "status_changed", next_status.value)
