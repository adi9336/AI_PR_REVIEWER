"""contracts — input/output contracts for specialist agents.

AgentInput bundles everything an agent needs: the diff, retrieved
context, and metadata. AgentOutput is the structured result: a list
of Findings (never raw prose).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from backend.models.enums import AgentType
from backend.models.findings import Finding


class AgentInput(BaseModel):
    """Everything a specialist agent needs to run."""

    review_id: UUID
    repo: str
    diff: str = Field(description="The PR diff to review")
    context_chunks: list[str] = Field(
        default_factory=list,
        description="Retrieved code chunks for grounding (pre-formatted)",
    )
    pr_number: int | None = None
    head_sha: str | None = None


class AgentOutput(BaseModel):
    """Structured output from a specialist agent — never raw prose."""

    agent_type: AgentType
    findings: list[Finding] = Field(default_factory=list)
    raw_response: str | None = None  # for debugging only


class LlmFindingPayload(BaseModel):
    """The JSON structure the LLM must return for findings."""

    findings: list[dict[str, Any]] = Field(default_factory=list)