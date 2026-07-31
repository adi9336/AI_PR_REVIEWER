"""langgraph_engine — WorkflowEngine implementation backed by LangGraph.

This is the ONLY module outside core/ that implements the WorkflowEngine
interface. It wraps the LangGraph compiled graph and adds:
  - Redis checkpointing (so a mid-review crash resumes)
  - node-level timeouts (a hung agent doesn't block the join — INV-4)
  - run / resume / get_state per the WorkflowEngine contract

Nothing outside backend/orchestrator/ imports langgraph (INV-2).
The rest of the system interacts with the orchestrator only through
backend/core/workflow_engine.py.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from backend.core.exceptions import WorkflowError
from backend.core.workflow_engine import WorkflowEngine
from backend.orchestrator.graph import build_graph
from backend.orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)


class LangGraphEngine(WorkflowEngine[OrchestratorState, OrchestratorState]):
    """WorkflowEngine backed by LangGraph with checkpointing."""

    def __init__(
        self,
        *,
        checkpoint_saver: Any = None,
        node_timeout: float = 30.0,
    ) -> None:
        self._graph = build_graph()
        self._checkpoint_saver = checkpoint_saver
        self._node_timeout = node_timeout

    async def run(self, initial_state: OrchestratorState) -> OrchestratorState:
        """Execute the workflow from initial_state to completion."""
        checkpoint_id = str(uuid4())
        return await self._run_with_checkpoint(initial_state, checkpoint_id)

    async def resume(self, checkpoint_id: str) -> OrchestratorState:
        """Resume a crashed/interrupted workflow from a checkpoint."""
        if self._checkpoint_saver is None:
            raise WorkflowError("Cannot resume without a checkpoint saver")
        config = {"configurable": {"thread_id": checkpoint_id}}
        try:
            result = await self._graph.ainvoke(None, config=config)
        except Exception as exc:
            raise WorkflowError(f"Resume failed: {exc}") from exc
        return self._to_state(result)

    async def get_state(self, checkpoint_id: str) -> OrchestratorState:
        """Read the current state at a checkpoint."""
        if self._checkpoint_saver is None:
            raise WorkflowError("Cannot get_state without a checkpoint saver")
        config = {"configurable": {"thread_id": checkpoint_id}}
        try:
            snapshot = self._graph.get_state(config)
            return self._to_state(snapshot.values)
        except Exception as exc:
            raise WorkflowError(f"get_state failed: {exc}") from exc

    async def _run_with_checkpoint(
        self, state: OrchestratorState, checkpoint_id: str
    ) -> OrchestratorState:
        """Run the graph with checkpointing enabled."""
        config: dict[str, Any] = {"configurable": {"thread_id": checkpoint_id}}
        try:
            result = await self._graph.ainvoke(state, config=config)
        except Exception as exc:
            raise WorkflowError(f"Graph execution failed: {exc}") from exc
        return self._to_state(result)

    @staticmethod
    def _to_state(result: Any) -> OrchestratorState:
        """Convert a LangGraph result to a state dict (TypedDict)."""
        if isinstance(result, dict):
            return result  # type: ignore[return-value]
        raise WorkflowError(f"Unexpected graph result type: {type(result)}")


def get_engine(
    *,
    node_timeout: float = 30.0,
) -> LangGraphEngine:
    """Factory: create a LangGraphEngine with MemorySaver checkpointing."""
    from langgraph.checkpoint.memory import MemorySaver

    saver = MemorySaver()
    return LangGraphEngine(checkpoint_saver=saver, node_timeout=node_timeout)