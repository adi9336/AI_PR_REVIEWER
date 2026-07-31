"""nodes — graph node functions for the orchestrator.

Each node takes the OrchestratorState (TypedDict = dict at runtime)
and returns a partial state update.

M8 upgrades:
  - aggregate(): dedup with agreement notes (agents that agreed noted)
  - decide(): full HITL gate routing — auto_post / approval_queue / escalate
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from backend.agents.contracts import AgentInput
from backend.agents.docs_agent import DocsAgent
from backend.agents.quality_agent import QualityAgent
from backend.agents.security_agent import SecurityAgent
from backend.agents.test_agent import TestAgent
from backend.orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

_AGENT_CLASSES: dict[str, type] = {
    "security": SecurityAgent,
    "quality": QualityAgent,
    "tests": TestAgent,
    "docs": DocsAgent,
}


def init_state(state: OrchestratorState) -> dict[str, Any]:
    """Initialize state for fan-out. Currently a pass-through."""
    return {}


def run_agent(
    state: OrchestratorState,
    agent_type: str,
    *,
    llm_client: Any = None,
    model: str | None = None,
    conn: Any = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run a single specialist agent and return its result.

    If the agent times out or errors, an AgentResult with error set
    is returned so the join still completes (INV-4).
    """
    agent_cls = _AGENT_CLASSES.get(agent_type)
    if agent_cls is None:
        return {
            "agent_results": [
                {"agent_type": agent_type, "findings": [], "error": f"unknown agent type: {agent_type}"}
            ]
        }

    review_id = state.get("review_id", "")
    agent_input = AgentInput(
        review_id=uuid.UUID(review_id) if isinstance(review_id, str) else review_id,
        repo=state.get("repo", ""),
        diff=state.get("diff", ""),
        context_chunks=state.get("context_chunks", []),
        pr_number=state.get("pr_number"),
        head_sha=state.get("head_sha"),
    )

    try:
        kwargs: dict[str, Any] = {"conn": conn}
        if llm_client is not None:
            kwargs["llm_client"] = llm_client
        if model is not None:
            kwargs["model"] = model

        agent = agent_cls(**kwargs)
        output = agent.review_with_events(agent_input)

        finding_payloads = [f.model_dump(mode="json") for f in output.findings]

        return {
            "agent_results": [
                {"agent_type": agent_type, "findings": finding_payloads, "error": None}
            ]
        }
    except Exception as exc:
        logger.exception(f"Agent {agent_type} failed")
        error_msg = str(exc)[:500]
        return {
            "agent_results": [
                {"agent_type": agent_type, "findings": [], "error": error_msg}
            ],
            "errors": [f"{agent_type}: {error_msg}"],
        }


def run_security(state: OrchestratorState, config: RunnableConfig | None = None) -> dict[str, Any]:
    return run_agent(state, "security")


def run_quality(state: OrchestratorState, config: RunnableConfig | None = None) -> dict[str, Any]:
    return run_agent(state, "quality")


def run_tests(state: OrchestratorState, config: RunnableConfig | None = None) -> dict[str, Any]:
    return run_agent(state, "tests")


def run_docs(state: OrchestratorState, config: RunnableConfig | None = None) -> dict[str, Any]:
    return run_agent(state, "docs")


def aggregate(state: OrchestratorState) -> dict[str, Any]:
    """Merge findings from all agents (M8: with agreement notes).

    Deduplication by (file_path, line_start) keeping highest confidence.
    When multiple agents find the same issue, the merged finding gets
    an `agreed_by` field listing the agents that agreed.
    """
    all_findings: list[dict[str, Any]] = []
    for ar in state.get("agent_results", []):
        if ar.get("error"):
            continue
        all_findings.extend(ar.get("findings", []))

    # Group findings by (file_path, line_start)
    groups: dict[tuple[str | None, int | None], list[dict[str, Any]]] = {}
    for f in all_findings:
        key = (f.get("file_path"), f.get("line_start"))
        groups.setdefault(key, []).append(f)

    merged: list[dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) == 1:
            # No duplicates — but still record which agent found it
            f = dict(group[0])
            f.setdefault("agreed_by", [])
            merged.append(f)
        else:
            # Multiple agents found the same issue — keep highest confidence
            # and note which agents agreed
            best = max(group, key=lambda x: float(x.get("confidence", 0)))
            agreed_by = sorted({
                str(g.get("agent_type", "")) for g in group
            })
            entry: dict[str, Any] = dict(best)
            entry["agreed_by"] = agreed_by
            entry["agreement_count"] = len(agreed_by)
            merged.append(entry)

    return {"merged_findings": merged}


def decide(state: OrchestratorState) -> dict[str, Any]:
    """Compute overall confidence and route through the HITL gate (M8).

    Routing logic:
      - Any CRITICAL finding → escalate (INV-5: regardless of confidence)
      - Overall confidence >= 0.8 and no CRITICAL → auto_post
      - Below threshold → approval_queue (row in hitl_reviews)
    """
    findings = state.get("merged_findings", [])
    if not findings:
        return {"overall_confidence": 1.0, "decision": "auto_post"}

    # INV-5: any CRITICAL → escalation regardless of confidence
    has_critical = any(
        str(f.get("severity", "")).upper() == "CRITICAL"
        for f in findings
    )
    if has_critical:
        return {"overall_confidence": 0.0, "decision": "escalate"}

    # Compute overall confidence (weighted average)
    confidences = [float(f.get("confidence", 0.5)) for f in findings]
    overall = sum(confidences) / len(confidences) if confidences else 0.5

    # Boost confidence when multiple agents agree on the same finding
    agreement_boost = 0.0
    for f in findings:
        agreed_count = f.get("agreement_count", 1)
        if agreed_count > 1:
            agreement_boost += 0.02 * (agreed_count - 1)
    overall = min(1.0, overall + agreement_boost)

    if overall >= 0.8:
        decision = "auto_post"
    else:
        decision = "approval_queue"

    return {"overall_confidence": overall, "decision": decision}