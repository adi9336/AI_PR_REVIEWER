"""dispute — humans can push back on findings (M18, Phase 19 partial).

A dispute is an anchored append-only event (agent=hitl,
event_type="dispute") — the audit spine's own contract (INV-6). The
payload carries the finding id + the reviewer's reason, so a dispute is
forever traceable to the finding and its explanation (M14 explain).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from backend.models.enums import EventType
from backend.observability.audit import query_audit
from backend.observability.events import emit_agent_event


def record_dispute(
    review_id: UUID | str,
    finding_id: UUID | str,
    reason: str,
    *,
    reviewer: str = "",
    conn: Any = None,
) -> UUID:
    """Record a human dispute of a finding as an append-only event."""
    return emit_agent_event(
        str(review_id),
        "hitl",
        EventType.DISPUTE,
        payload={
            "finding_id": str(finding_id),
            "reason": reason[:500],
            "reviewer": reviewer[:100],
        },
        conn=conn,
    )


def list_disputes(
    review_id: UUID | str,
    *,
    conn: Any = None,
) -> list[dict[str, Any]]:
    """All disputes recorded for a review (time-ordered, masked)."""
    return query_audit(review_id=review_id, event_type="dispute", conn=conn)
