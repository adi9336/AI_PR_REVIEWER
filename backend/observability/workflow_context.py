"""workflow_context — review-scoped event emission helpers.

Provides a ReviewContext that bundles a review_id + connection and
offers convenience methods for emitting spans, llm.call, tool.call,
and decision events without repeating the review_id and agent args
on every call.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID

from backend.models.enums import EventType, Outcome
from backend.observability.events import emit_agent_event, emit_span


class ReviewContext:
    """Scopes event emission to one review_id and one connection.

    Usage:
        ctx = ReviewContext(review_id, conn=conn)
        with ctx.span("security") as span_id:
            ctx.llm_call("security", model="kimi-k3", tokens_in=500,
                         tokens_out=200, cost_usd=0.01, latency_ms=120)
            ctx.decision("security", outcome=Outcome.APPROVED, confidence=0.92)
    """

    def __init__(self, review_id: UUID | str, conn: Any = None) -> None:
        self.review_id = review_id
        self.conn = conn

    @contextmanager
    def span(self, agent: str, *, model: str | None = None) -> Iterator[UUID]:
        """Open a span for this review. Emits span.start + span.end."""
        with emit_span(self.review_id, agent, model=model, conn=self.conn) as span_id:
            yield span_id

    def llm_call(
        self,
        agent: str,
        *,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: Decimal | float,
        latency_ms: int,
        confidence: Decimal | float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> UUID:
        """Emit an llm.call event. cost_usd and latency_ms are required."""
        return emit_agent_event(
            self.review_id,
            agent,
            EventType.LLM_CALL,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            confidence=confidence,
            payload=payload,
            conn=self.conn,
        )

    def tool_call(
        self,
        agent: str,
        *,
        latency_ms: int,
        tool: str,
        payload: dict[str, Any] | None = None,
    ) -> UUID:
        """Emit a tool.call event."""
        return emit_agent_event(
            self.review_id,
            agent,
            EventType.TOOL_CALL,
            latency_ms=latency_ms,
            payload={"tool": tool, **(payload or {})},
            conn=self.conn,
        )

    def decision(
        self,
        agent: str,
        *,
        outcome: Outcome | str,
        confidence: Decimal | float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> UUID:
        """Emit a decision event (approved / request_changes / critical_block / escalated)."""
        return emit_agent_event(
            self.review_id,
            agent,
            EventType.DECISION,
            outcome=outcome,
            confidence=confidence,
            payload=payload,
            conn=self.conn,
        )

    def escalation(
        self,
        agent: str,
        *,
        reason: str,
        confidence: Decimal | float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> UUID:
        """Emit an escalation event."""
        return emit_agent_event(
            self.review_id,
            agent,
            EventType.ESCALATION,
            outcome=Outcome.ESCALATED,
            confidence=confidence,
            payload={"reason": reason, **(payload or {})},
            conn=self.conn,
        )