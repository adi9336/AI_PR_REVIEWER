"""Observability package — events spine, tracing, and audit trail.

M4: every action is one append-only row.
"""

from backend.observability.events import emit_agent_event, emit_span, get_events_for_review
from backend.observability.tracing import current_span_id, parent_span_id, start_span
from backend.observability.workflow_context import ReviewContext

__all__ = [
    "emit_agent_event",
    "emit_span",
    "get_events_for_review",
    "current_span_id",
    "parent_span_id",
    "start_span",
    "ReviewContext",
]