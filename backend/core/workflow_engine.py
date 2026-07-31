"""Abstract workflow engine interface.

This is the ONLY integration point between the orchestrator and the
LangGraph runtime. No module outside ``backend/orchestrator/`` imports
langgraph directly — everything goes through this interface. (INV-2.)

This module is in the innermost layer (core). It imports NOTHING.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

StateT = TypeVar("StateT")  # The state object the workflow operates on
ResultT = TypeVar("ResultT")  # The result the workflow produces


class WorkflowEngine(ABC, Generic[StateT, ResultT]):
    """Abstract orchestration interface.

    The orchestrator calls ``run`` / ``resume`` / ``get_state`` and never
    touches the underlying engine (LangGraph, Temporal, etc.) directly.
    """

    @abstractmethod
    async def run(self, initial_state: StateT) -> ResultT:
        """Execute the workflow from ``initial_state`` to completion."""
        ...

    @abstractmethod
    async def resume(self, checkpoint_id: str) -> ResultT:
        """Resume a crashed/interrupted workflow from a checkpoint."""
        ...

    @abstractmethod
    async def get_state(self, checkpoint_id: str) -> StateT:
        """Read the current state at ``checkpoint_id``."""
        ...