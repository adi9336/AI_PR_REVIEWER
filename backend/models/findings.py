"""Finding — the structured output of every specialist agent.

A Finding is never raw prose: it has a severity, a file location, a
confidence score, and a rationale that can be audited and disputed.
This module imports nothing but stdlib + pydantic. (INV-1.)
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.models.enums import AgentType, Severity


class Finding(BaseModel):
    """A single issue found by a specialist agent."""

    id: UUID | None = None
    review_id: UUID | None = None  # set by the aggregator after merge
    agent_type: AgentType
    severity: Severity
    category: str = Field(description="e.g. 'sql-injection', 'missing-test', 'doc-drift'")
    summary: str = Field(description="one-line human-readable summary")
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    suggestion: str | None = None
    confidence: Decimal = Field(
        ge=Decimal("0"), le=Decimal("1"),
        description="0.000-1.000 — how confident the agent is in this finding",
    )
    rationale: str = Field(description="why the agent believes this finding is correct")
    created_at: str | None = None  # ISO timestamp, set by the DB layer