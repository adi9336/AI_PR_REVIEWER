"""graph — LangGraph orchestrator with parallel fan-out via Send API.

The graph structure:
  START → init → fan_out (Send to 4 agents in parallel) → aggregate → decide → END

Nothing outside backend/orchestrator/ imports langgraph (INV-2).
The WorkflowEngine interface in backend/core/workflow_engine.py is
the only integration point for the rest of the system.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from backend.orchestrator.nodes import (
    aggregate,
    decide,
    init_state,
    run_docs,
    run_quality,
    run_security,
    run_tests,
)
from backend.orchestrator.state import OrchestratorState

AGENT_NAMES = ["security", "quality", "tests", "docs"]


def fan_out(state: dict[str, Any]) -> list[Send]:
    """Dispatch to all 4 specialist agents in parallel via Send API."""
    sends: list[Send] = []
    for agent_name in AGENT_NAMES:
        sends.append(Send(f"agent_{agent_name}", state))
    return sends


def build_graph() -> Any:
    """Build and compile the orchestrator graph."""
    graph = StateGraph(OrchestratorState)

    graph.add_node("init", init_state)
    graph.add_node("agent_security", run_security)
    graph.add_node("agent_quality", run_quality)
    graph.add_node("agent_tests", run_tests)
    graph.add_node("agent_docs", run_docs)
    graph.add_node("aggregate", aggregate)
    graph.add_node("decide", decide)

    graph.add_edge(START, "init")

    graph.add_conditional_edges(
        "init",
        fan_out,
        {
            "agent_security": "agent_security",
            "agent_quality": "agent_quality",
            "agent_tests": "agent_tests",
            "agent_docs": "agent_docs",
        },
    )

    graph.add_edge("agent_security", "aggregate")
    graph.add_edge("agent_quality", "aggregate")
    graph.add_edge("agent_tests", "aggregate")
    graph.add_edge("agent_docs", "aggregate")
    graph.add_edge("aggregate", "decide")
    graph.add_edge("decide", END)

    return graph.compile()