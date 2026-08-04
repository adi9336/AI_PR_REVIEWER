"""feedback — human feedback on findings (M18, Phase 19 partial).

Like disputes, feedback is an anchored append-only event (agent=hitl,
event_type="feedback"). The `helpful` flag is the raw signal for the
continuous-learning loop: disputed/unhelpful findings are golden-set
expansion candidates (Phase 20).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from backend.models.enums import EventType
from backend.observability.audit import query_audit
from backend.observability.events import emit_agent_event


def record_feedback(
    review_id: UUID | str,
    finding_id: UUID | str,
    helpful: bool,
    *,
    note: str = "",
    conn: Any = None,
) -> UUID:
    """Record whether a human found a finding helpful (append-only event)."""
    return emit_agent_event(
        str(review_id),
        "hitl",
        EventType.FEEDBACK,
        payload={
            "finding_id": str(finding_id),
            "helpful": bool(helpful),
            "note": note[:500],
        },
        conn=conn,
    )


def list_feedback(
    review_id: UUID | str,
    *,
    conn: Any = None,
) -> list[dict[str, Any]]:
    """All feedback events for a review (time-ordered, masked)."""
    return query_audit(review_id=review_id, event_type="feedback", conn=conn)
