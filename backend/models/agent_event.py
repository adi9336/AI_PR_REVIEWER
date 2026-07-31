"""AgentEvent — one append-only row in the events spine.

Every action the system takes — a span opening, an LLM call, a tool call,
a routing decision — lands here as a single row. A review_id reconstructs
the full trace in time order (M4).

This module imports nothing but stdlib + pydantic. (INV-1.)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from backend.models.enums import EventType, Outcome


class AgentEvent(BaseModel):
    """A single event in the audit trail (maps 1:1 to an agent_events row).

    Required fields are the minimum to INSERT a row; the database schema
    enforces additional constraints (e.g. llm.call must carry cost_usd and
    latency_ms — the agent_events_llm_call_accountable CHECK).
    """

    review_id: UUID
    agent: str = Field(description="security|quality|tests|docs|aggregator|orchestrator")
    event_type: EventType
    span_id: UUID | None = None
    parent_span: UUID | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    outcome: Outcome | None = None
    confidence: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=Decimal("1"),
        description="0.000-1.000 — confidence at the point this event was recorded",
    )
    payload: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}