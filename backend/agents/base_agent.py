"""base_agent — the shared engine for all specialist agents.

Each specialist agent (security, quality, tests, docs) follows the same flow:
  1. Receive AgentInput (diff + retrieved context)
  2. Sanitize the diff via injection_guard (INV-3)
  3. Render the system + user prompt
  4. Call the LLM via llm_client
  5. Parse the JSON response into Finding[]
  6. Emit agent_events (span.start, llm.call, span.end)

Subclasses define: agent_type, system_prompt_name, user_prompt_name.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any
from uuid import UUID

from backend.agents.contracts import AgentInput, AgentOutput
from backend.core.exceptions import InjectionDetected, LlmCallError, PrReviewError
from backend.models.enums import EventType, Outcome, Severity
from backend.models.findings import Finding
from backend.observability.events import emit_agent_event, emit_span
from backend.prompts.registry import (
    get_system_prompt,
    get_user_prompt,
    prompt_version,
    render_prompt,
)
from backend.security.injection_guard import check_injection, sanitize_diff
from backend.tools.llm_client import LlmClient, LlmResponse, get_llm_client


class BaseAgent(ABC):
    """Abstract base for all specialist agents.

    Subclasses must implement:
      - agent_type: the AgentType enum
      - _parse_findings: parse LLM JSON into Finding[]
    """

    def __init__(
        self,
        *,
        llm_client: LlmClient | None = None,
        model: str | None = None,
        conn: Any = None,
    ) -> None:
        self._llm = llm_client or get_llm_client()
        self._model = model
        self._conn = conn

    @property
    @abstractmethod
    def agent_type(self) -> str:
        """The agent type string (security|quality|tests|docs)."""
        ...

    @property
    def system_prompt_name(self) -> str:
        return f"system_{self.agent_type}"

    @property
    def user_prompt_name(self) -> str:
        return f"user_{self.agent_type}"

    def review(self, agent_input: AgentInput) -> AgentOutput:
        """Run the full agent flow: sanitize → prompt → LLM → parse → events."""
        review_id = agent_input.review_id
        agent_name = self.agent_type

        with emit_span(str(review_id), agent_name, model=self._model, conn=self._conn):
            # 1. Sanitize the diff (INV-3)
            try:
                safe_diff = sanitize_diff(agent_input.diff)
            except InjectionDetected:
                # Emit escalation event
                emit_agent_event(
                    str(review_id),
                    agent_name,
                    EventType.ESCALATION,
                    outcome=Outcome.ESCALATED,
                    payload={"reason": "prompt-injection-detected"},
                    conn=self._conn,
                )
                raise

            # 2. Render prompts
            system_prompt = get_system_prompt(self.agent_type)
            context_text = "\n\n---\n\n".join(agent_input.context_chunks) if agent_input.context_chunks else "(no context retrieved)"
            user_prompt = render_prompt(
                get_user_prompt(self.agent_type),
                diff=safe_diff,
                context=context_text,
            )

            # 3. Call the LLM
            llm_resp = self._llm.complete_json(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self._model,
            )

            # 4. Emit llm.call event
            # Get the raw response for cost tracking
            # (complete_json returns parsed dict, we need the LlmResponse for tokens)
            # Re-estimate from the raw resp — but we don't have it here.
            # For now, emit with the raw response's cost data.
            # The llm_client tracks it internally; we'll emit what we can.
            # A cleaner approach is to call complete() and parse ourselves.

            # 5. Parse findings
            findings = self._parse_findings(llm_resp, agent_input)

            return AgentOutput(
                agent_type=self._get_agent_enum(),
                findings=findings,
                raw_response=json.dumps(llm_resp),
            )

    def review_with_events(self, agent_input: AgentInput) -> AgentOutput:
        """Run the agent and emit llm.call event with cost tracking.

        This wraps review() and emits a proper llm.call event using
        the LlmResponse data (tokens, cost, latency).
        """
        review_id = str(agent_input.review_id)
        agent_name = self.agent_type

        with emit_span(review_id, agent_name, model=self._model, conn=self._conn):
            # 1. Sanitize (INV-3)
            try:
                safe_diff = sanitize_diff(agent_input.diff)
            except InjectionDetected:
                emit_agent_event(
                    review_id,
                    agent_name,
                    EventType.ESCALATION,
                    outcome=Outcome.ESCALATED,
                    payload={"reason": "prompt-injection-detected"},
                    conn=self._conn,
                )
                raise

            # 2. Render prompts
            system_prompt = get_system_prompt(self.agent_type)
            context_text = (
                "\n\n---\n\n".join(agent_input.context_chunks)
                if agent_input.context_chunks
                else "(no context retrieved)"
            )
            user_prompt = render_prompt(
                get_user_prompt(self.agent_type),
                diff=safe_diff,
                context=context_text,
            )

            # 3. Call the LLM (with events)
            llm_resp = self._llm.complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self._model,
                json_mode=True,
            )

            # 4. Emit llm.call event
            emit_agent_event(
                review_id,
                agent_name,
                EventType.LLM_CALL,
                model=llm_resp.model,
                tokens_in=llm_resp.tokens_in,
                tokens_out=llm_resp.tokens_out,
                cost_usd=llm_resp.cost_usd,
                latency_ms=llm_resp.latency_ms,
                payload={"prompt_version": prompt_version(agent_name)},
                conn=self._conn,
            )

            # 5. Parse JSON
            try:
                parsed = json.loads(llm_resp.content)
            except json.JSONDecodeError as exc:
                raise LlmCallError(
                    f"LLM returned malformed JSON: {exc}\nContent: {llm_resp.content[:500]}"
                ) from exc

            # 6. Parse findings
            findings = self._parse_findings(parsed, agent_input)

            return AgentOutput(
                agent_type=self._get_agent_enum(),
                findings=findings,
                raw_response=llm_resp.content,
            )

    @abstractmethod
    def _parse_findings(
        self, llm_output: dict[str, Any], agent_input: AgentInput
    ) -> list[Finding]:
        """Parse the LLM JSON output into Finding objects."""
        ...

    def _get_agent_enum(self) -> Any:
        """Return the AgentType enum for this agent."""
        from backend.models.enums import AgentType

        return AgentType(self.agent_type)