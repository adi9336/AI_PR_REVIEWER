"""tracing — span context for the events spine.

Every action belongs to a span, and every span has a parent span
(except the root). This module provides a contextvar-based span
stack so ``emit_agent_event`` can automatically populate
``parent_span`` without the caller threading it manually.

The span hierarchy supports M4's success criterion: a simulated review
emits span.start and matching span.end with a parent_span chain.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator
from uuid import UUID, uuid4

# The current span stack. Each entry is a span_id.
# The bottom of the stack (index 0) is the root span.
_span_stack: contextvars.ContextVar[list[UUID]] = contextvars.ContextVar(
    "span_stack", default=[]
)


def current_span_id() -> UUID | None:
    """Return the current (innermost) span_id, or None if no span is active."""
    stack = _span_stack.get()
    return stack[-1] if stack else None


def parent_span_id() -> UUID | None:
    """Return the parent span_id for a new span.

    This is the current span if one is active, or None at the root.
    """
    return current_span_id()


@contextmanager
def start_span(span_id: UUID | None = None) -> Iterator[UUID]:
    """Open a new span context.

    Generates a UUID if none is provided. The span_id is pushed onto the
    context stack and popped on exit. Caller should emit span.start on
    entry and span.end on exit.

    Usage:
        with start_span() as sid:
            emit_agent_event(review_id, agent, EventType.SPAN_START, span_id=sid)
            ...
            emit_agent_event(review_id, agent, EventType.SPAN_END, span_id=sid)
    """
    sid = span_id or uuid4()
    stack = list(_span_stack.get())
    stack.append(sid)
    token = _span_stack.set(stack)
    try:
        yield sid
    finally:
        stack = list(_span_stack.get())
        if stack and stack[-1] == sid:
            stack.pop()
        _span_stack.reset(token)


def root_span_id() -> UUID | None:
    """Return the root span_id (bottom of the stack), or None."""
    stack = _span_stack.get()
    return stack[0] if stack else None