"""Domain exceptions for the AI PR review agent.

This module is in the innermost layer (core). It may NOT import anything
outside of stdlib. (INV-1: dependencies point inward only.)
"""

from __future__ import annotations


class PrReviewError(Exception):
    """Base exception for all PR review agent errors."""


class WorkflowError(PrReviewError):
    """Workflow orchestration failure (graph crash, checkpoint corruption)."""


class LlmCallError(PrReviewError):
    """An LLM call failed (timeout, rate limit, malformed response)."""


class InjectionDetected(PrReviewError):
    """The untrusted diff contained prompt-injection content (INV-3)."""


class BudgetExceeded(PrReviewError):
    """The daily cost cap has been exceeded — no further LLM calls (ADR-004)."""


class IdempotencyError(PrReviewError):
    """A retried webhook delivery was detected and rejected (INV-5)."""


class RetrievalError(PrReviewError):
    """Hybrid retrieval (ANN or FTS) failed."""


class CircuitOpen(PrReviewError):
    """A circuit breaker is open for an outbound dependency (INV-4)."""