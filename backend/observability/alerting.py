"""alerting — anchored alerts on the append-only spine (M16, Phase 20).

An alert is an agent_events row (event_type="alert", agent="alerting").
agent_events.review_id is NOT NULL by INV-6, so every alert is anchored
to a real review — system-level drift is a report (drift.py), never a
synthetic-UUID event.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from backend.models.enums import EventType
from backend.observability.events import emit_agent_event


def emit_alert(
    review_id: UUID | str,
    level: str,
    metric: str,
    message: str,
    *,
    conn: Any = None,
) -> UUID:
    """Write an alert event for a review. Append-only, audit-visible."""
    return emit_agent_event(
        str(review_id),
        "alerting",
        EventType.ALERT,
        payload={"level": level, "metric": metric, "message": message},
        conn=conn,
    )


def alert_for_cost_spike(
    review_id: UUID | str,
    cost: float,
    cap: float,
    *,
    conn: Any = None,
) -> UUID:
    """Emit a WARNING alert when a single review blew past its cost cap."""
    return emit_alert(
        review_id,
        level="WARNING",
        metric="cost_per_review",
        message=(
            f"review cost ${cost:.4f} exceeds the per-review cap ${cap:.4f} "
            f"(+{(cost / cap - 1) * 100:.0f}%)"
        ),
        conn=conn,
    )
