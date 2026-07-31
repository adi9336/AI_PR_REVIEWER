"""events — emit append-only rows to the agent_events spine.

This is the heart of M4: every action is one append-only row. A review_id
reconstructs a full trace in time order via ``get_events_for_review``.

Schema (from 2026-06-tiger-init.sql):
  ts, review_id, agent, span_id, parent_span, event_type,
  model, tokens_in, tokens_out, cost_usd, latency_ms,
  outcome, confidence, payload

Key constraints:
  - llm.call rows MUST carry cost_usd and latency_ms (CHECK constraint).
  - UPDATE/DELETE/TRUNCATE are rejected by triggers (INV-6).
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID

from backend.database.postgres import get_connection
from backend.models.enums import EventType, Outcome
from backend.observability.tracing import parent_span_id, start_span

__all__ = [
    "emit_agent_event",
    "get_events_for_review",
    "emit_span",
]

# Sentinel: distinguishes "parent_span not provided" from "parent_span=None".
_UNSET = object()


def emit_agent_event(
    review_id: UUID | str,
    agent: str,
    event_type: EventType | str,
    *,
    span_id: UUID | None = None,
    parent_span: Any = _UNSET,
    model: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: Decimal | float | None = None,
    latency_ms: int | None = None,
    outcome: Outcome | str | None = None,
    confidence: Decimal | float | None = None,
    payload: dict[str, Any] | None = None,
    conn: Any = None,
) -> UUID:
    """Append one event row to agent_events. Returns the span_id.

    If span_id is None, a new UUID is generated. If parent_span is not
    provided (left as _UNSET) and event_type is span.start, the current
    context span is used as the parent (if any). Pass parent_span=None
    explicitly to force a root span with no parent.

    Pass an open ``conn`` to reuse a connection; otherwise a new one is
    opened and closed internally.

    Raises ValueError if an llm.call lacks cost_usd or latency_ms (the
    database CHECK would also reject this, but failing early gives a
    better error).
    """
    # Normalize event_type to string
    et = event_type.value if isinstance(event_type, EventType) else str(event_type)

    # Enforce llm.call accountability client-side too (defense-in-depth
    # with the DB CHECK constraint).
    if et == EventType.LLM_CALL.value:
        if cost_usd is None or latency_ms is None:
            raise ValueError(
                f"llm.call event must carry cost_usd and latency_ms "
                f"(agent={agent}, review_id={review_id})"
            )

    # Resolve span_id
    sid = span_id or uuid.uuid4()

    # Auto-populate parent_span from context only if not explicitly provided.
    # parent_span=None means "this is a root span" — don't override it.
    if parent_span is _UNSET:
        pspan = parent_span_id() if et == EventType.SPAN_START.value else None
    else:
        pspan = parent_span

    # Normalize outcome
    outcome_str: str | None = None
    if outcome is not None:
        outcome_str = outcome.value if isinstance(outcome, Outcome) else str(outcome)

    # Normalize review_id to UUID
    rid = uuid.UUID(str(review_id))

    # Convert Decimal
    cost_val = Decimal(str(cost_usd)) if cost_usd is not None else None
    conf_val = Decimal(str(confidence)) if confidence is not None else None

    sql = """
        INSERT INTO agent_events (
            ts, review_id, agent, span_id, parent_span, event_type,
            model, tokens_in, tokens_out, cost_usd, latency_ms,
            outcome, confidence, payload
        ) VALUES (
            now(), %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s
        )
    """
    params = (
        rid,
        agent,
        sid,
        pspan,
        et,
        model,
        tokens_in,
        tokens_out,
        cost_val,
        latency_ms,
        outcome_str,
        conf_val,
        json.dumps(payload) if payload else None,
    )

    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    else:
        with get_connection() as conn2:
            with conn2.cursor() as cur:
                cur.execute(sql, params)

    return sid


def get_events_for_review(
    review_id: UUID | str, conn: Any = None
) -> list[dict[str, Any]]:
    """Retrieve all events for a review_id in time order (ts ASC).

    This is the primary read path for the audit trail: a review_id
    reconstructs the full trace.
    """
    rid = uuid.UUID(str(review_id))
    sql = """
        SELECT ts, review_id, agent, span_id, parent_span, event_type,
               model, tokens_in, tokens_out, cost_usd, latency_ms,
               outcome, confidence, payload
        FROM agent_events
        WHERE review_id = %s
        ORDER BY ts ASC
    """
    rows: list[tuple[Any, ...]] = []
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (rid,))
            rows = cur.fetchall()
    else:
        with get_connection() as conn2:
            with conn2.cursor() as cur:
                cur.execute(sql, (rid,))
                rows = cur.fetchall()

    return [
        {
            "ts": row[0],
            "review_id": row[1],
            "agent": row[2],
            "span_id": row[3],
            "parent_span": row[4],
            "event_type": row[5],
            "model": row[6],
            "tokens_in": row[7],
            "tokens_out": row[8],
            "cost_usd": row[9],
            "latency_ms": row[10],
            "outcome": row[11],
            "confidence": row[12],
            "payload": row[13],
        }
        for row in rows
    ]


@contextmanager
def emit_span(
    review_id: UUID | str,
    agent: str,
    *,
    model: str | None = None,
    conn: Any = None,
) -> Iterator[UUID]:
    """Context manager that emits span.start on entry and span.end on exit.

    Captures the parent span BEFORE entering start_span, otherwise
    parent_span_id() would return the just-pushed span itself.

    Usage:
        with emit_span(review_id, "security") as span_id:
            emit_agent_event(review_id, "security", EventType.LLM_CALL, ...)
    """
    parent = parent_span_id()
    with start_span() as span_id:
        emit_agent_event(
            review_id,
            agent,
            EventType.SPAN_START,
            span_id=span_id,
            parent_span=parent,
            model=model,
            conn=conn,
        )
        try:
            yield span_id
        finally:
            emit_agent_event(
                review_id,
                agent,
                EventType.SPAN_END,
                span_id=span_id,
                conn=conn,
            )