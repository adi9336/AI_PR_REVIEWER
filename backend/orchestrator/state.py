"""state — the orchestrator's shared state for LangGraph.

The state flows through the graph:
  1. init → review_id, repo, diff, context
  2. fan-out → each agent reads input, writes its findings
  3. join → aggregator merges all findings
  4. decision → overall confidence + routing decision

Uses TypedDict + Annotated[list, add_list] so LangGraph's parallel fan-out
nodes can each append to agent_results without a LastValue conflict.
Fields without a reducer annotation default to LastValue (overwrite).
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def add_list(left: list[Any], right: list[Any]) -> list[Any]:
    """Reducer: concatenate two lists (for parallel fan-out accumulation)."""
    return left + right


class AgentResult(TypedDict):
    """One agent's output in the shared state."""

    agent_type: str
    findings: list[dict[str, Any]]
    error: str | None


class OrchestratorState(TypedDict):
    """The shared state that flows through the LangGraph.

    The `agent_results` field uses the `add_list` reducer so parallel agent
    nodes can each append their result without a concurrent-write error.
    Other fields use the default LastValue reducer (overwrite on update).
    """

    review_id: str
    repo: str
    diff: str
    context_chunks: list[str]
    pr_number: int | None
    head_sha: str | None

    # Fan-out: each agent node appends its result here (reducer: add_list)
    agent_results: Annotated[list[AgentResult], add_list]

    # Join: merged findings after all agents complete
    merged_findings: list[dict[str, Any]]

    # Decision: overall confidence + routing
    overall_confidence: float | None
    decision: str | None  # auto_post | approval_queue | escalate

    # Error tracking (reducer: add_list for parallel accumulation)
    errors: Annotated[list[str], add_list]

    # Metadata
    model: str | None