"""llm_client — OpenAI-compatible LLM client wrapper.

Wraps OpenAI chat.completions.create with:
  - Structured output (JSON mode) for agent responses
  - Token + cost tracking via emit_agent_event
  - Retry with exponential backoff
  - Configurable model (kimi-k3 / hy3 routing via ADR-0002)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from backend.core.exceptions import LlmCallError

# Approximate cost per 1M tokens (USD) — used for budget tracking.
# Updated when real pricing is available; these are estimates.
COST_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    # model: (input_cost_per_1M, output_cost_per_1M)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "kimi-k3": (0.30, 0.90),
    "hy3": (0.20, 0.60),
}
DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class LlmResponse:
    """The result of an LLM call."""

    content: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    raw: Any = None


def _calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate cost in USD based on token counts."""
    pricing = COST_PER_1M_TOKENS.get(model, (0.30, 0.90))
    return (tokens_in * pricing[0] + tokens_out * pricing[1]) / 1_000_000


class LlmClient:
    """Wraps the OpenAI client for LLM calls with cost tracking."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        from openai import OpenAI

        key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("OPENAI_API_KEY is required for LlmClient")
        kwargs: dict[str, Any] = {"api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client: Any = OpenAI(**kwargs)
        model_env = os.getenv("MODEL_REASONING", DEFAULT_MODEL)
        self._default_model: str = default_model if default_model else model_env

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        json_mode: bool = False,
        retries: int = 3,
    ) -> LlmResponse:
        """Call the LLM and return structured response with cost tracking.

        Raises LlmCallError after all retries are exhausted.
        """
        mdl = model or self._default_model
        kwargs: dict[str, Any] = {
            "model": mdl,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                start = time.monotonic()
                resp = self._client.chat.completions.create(**kwargs)
                latency = int((time.monotonic() - start) * 1000)

                content = resp.choices[0].message.content or ""
                usage = resp.usage
                tokens_in = usage.prompt_tokens if usage else 0
                tokens_out = usage.completion_tokens if usage else 0
                cost = _calculate_cost(mdl, tokens_in, tokens_out)

                return LlmResponse(
                    content=content,
                    model=mdl,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency,
                    cost_usd=cost,
                    raw=resp,
                )
            except Exception as exc:
                last_error = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)  # 1s, 2s, 4s
                continue

        raise LlmCallError(
            f"LLM call failed after {retries} retries: {last_error}"
        ) from last_error

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        retries: int = 3,
    ) -> dict[str, Any]:
        """Call the LLM with JSON mode and parse the response as JSON.

        Raises LlmCallError if the JSON is malformed.
        """
        resp = self.complete(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            retries=retries,
        )
        try:
            parsed: dict[str, Any] = json.loads(resp.content)
            return parsed
        except json.JSONDecodeError as exc:
            raise LlmCallError(
                f"LLM returned malformed JSON: {exc}\nContent: {resp.content[:500]}"
            ) from exc


def get_llm_client() -> LlmClient:
    """Factory: create an LlmClient from env vars."""
    return LlmClient()