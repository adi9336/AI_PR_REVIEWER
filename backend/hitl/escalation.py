"""escalation — CRITICAL finding escalation logic (INV-5).

Any CRITICAL finding → escalation regardless of confidence. The review
is routed to the HITL queue with reason 'critical_finding', and an
escalation event is emitted to the audit trail.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from backend.models.enums import EventType, Outcome
from backend.observability.events import emit_agent_event


def has_critical(findings: list[dict[str, Any]]) -> bool:
    """True if any finding has severity CRITICAL."""
    return any(
        str(f.get("severity", "")).upper() == "CRITICAL"
        for f in findings
    )


def escalate(
    review_id: UUID | str,
    reason: str,
    *,
    agent: str = "aggregator",
    conn: Any = None,
) -> UUID:
    """Emit an escalation event for the audit trail.

    Returns the span_id of the escalation event.
    """
    return emit_agent_event(
        str(review_id),
        agent,
        EventType.ESCALATION,
        outcome=Outcome.ESCALATED,
        payload={"reason": reason},
        conn=conn,
    )