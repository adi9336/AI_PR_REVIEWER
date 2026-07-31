"""M4 gate — the events spine: every action is one append-only row.

Asserts:
  1. A simulated review emits span.start / span.end with a parent_span chain.
  2. SELECT ... WHERE review_id=$1 ORDER BY ts returns events in time order.
  3. UPDATE / DELETE against agent_events is rejected (INV-6 immutability).
  4. llm.call rows carry cost + latency (client-side + DB CHECK).
  5. Non-LLM events may omit cost/latency.
  6. A full simulated review (4 agents + aggregator) produces a reconstructable trace.

Requires TIGER_DATABASE_URL (backend/.env). Skips cleanly if unset so the
suite stays runnable on a machine without the credential.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / "backend" / ".env")

TIGER_URL = os.getenv("TIGER_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live events spine tests"
)


@pytest.fixture(scope="module")
def conn():
    """Shared connection with autocommit so SELECTs don't hold locks."""
    with psycopg.connect(TIGER_URL, connect_timeout=30, autocommit=True) as c:
        yield c


@pytest.fixture
def review_id():
    """A fresh UUID for each test — isolates events."""
    return uuid4()


def _scalars(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


# ── 1. span.start + span.end with parent chain ──────────────────────────
def test_span_start_and_end_have_parent_chain(conn, review_id):
    """A span emits span.start then span.end; child spans link via parent_span."""
    from backend.observability.events import emit_span, emit_agent_event
    from backend.models.enums import EventType

    review_id_str = str(review_id)

    # Root span
    with emit_span(review_id_str, "orchestrator", conn=conn) as root_span:
        # Child span inside the root
        with emit_span(review_id_str, "security", conn=conn) as child_span:
            # Emit an llm.call inside the child span
            emit_agent_event(
                review_id_str, "security", EventType.LLM_CALL,
                model="kimi-k3", tokens_in=100, tokens_out=50,
                cost_usd=0.005, latency_ms=80, conn=conn,
            )

    # Read back
    events = _scalars(
        conn,
        "select event_type from agent_events where review_id = %s order by ts",
        (review_id,),
    )
    assert EventType.SPAN_START.value in events, "missing span.start"
    assert EventType.SPAN_END.value in events, "missing span.end"

    # Verify parent_span chain: the child span.start should have parent = root span
    with conn.cursor() as cur:
        cur.execute(
            "select span_id, parent_span from agent_events "
            "where review_id = %s and event_type = 'span.start' order by ts",
            (review_id,),
        )
        spans = cur.fetchall()
    assert len(spans) >= 2, f"expected >=2 span.start rows, got {len(spans)}"
    root_span_id, root_parent = spans[0][0], spans[0][1]
    child_span_id, child_parent = spans[1][0], spans[1][1]
    assert root_parent is None, "root span should have no parent"
    assert child_parent == root_span_id, (
        f"child span parent {child_parent} != root span {root_span_id}"
    )


# ── 2. events are returned in time order ────────────────────────────────
def test_events_returned_in_time_order(conn, review_id):
    """SELECT ... ORDER BY ts returns events in chronological order."""
    from backend.observability.events import emit_agent_event, get_events_for_review
    from backend.models.enums import EventType

    rid = str(review_id)
    # Emit several events with small delays to ensure distinct timestamps
    emit_agent_event(rid, "docs", EventType.SPAN_START, conn=conn)
    time.sleep(0.01)
    emit_agent_event(
        rid, "docs", EventType.LLM_CALL,
        model="hy3", tokens_in=200, tokens_out=100,
        cost_usd=0.002, latency_ms=50, conn=conn,
    )
    time.sleep(0.01)
    emit_agent_event(rid, "docs", EventType.SPAN_END, conn=conn)

    events = get_events_for_review(rid, conn=conn)
    assert len(events) == 3, f"expected 3 events, got {len(events)}"

    # Verify timestamps are monotonically non-decreasing
    timestamps = [e["ts"] for e in events]
    for i in range(1, len(timestamps)):
        assert timestamps[i] >= timestamps[i - 1], (
            f"events not in time order at index {i}: {timestamps[i]} < {timestamps[i-1]}"
        )

    # Verify the event_type sequence is correct
    assert events[0]["event_type"] == "span.start"
    assert events[1]["event_type"] == "llm.call"
    assert events[2]["event_type"] == "span.end"


# ── 3. INV-6: UPDATE and DELETE are rejected ─────────────────────────────
def test_update_rejected(conn, review_id):
    """INV-6: UPDATE against agent_events is rejected by the trigger."""
    from backend.observability.events import emit_agent_event
    from backend.models.enums import EventType

    rid = str(review_id)
    emit_agent_event(rid, "security", EventType.SPAN_START, conn=conn)

    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                cur.execute(
                    "update agent_events set cost_usd = 999 "
                    "where review_id = %s",
                    (review_id,),
                )


def test_delete_rejected(conn, review_id):
    """INV-6: DELETE against agent_events is rejected by the trigger."""
    from backend.observability.events import emit_agent_event
    from backend.models.enums import EventType

    rid = str(review_id)
    emit_agent_event(rid, "quality", EventType.SPAN_START, conn=conn)

    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                cur.execute(
                    "delete from agent_events where review_id = %s",
                    (review_id,),
                )


# ── 4. llm.call must carry cost + latency ────────────────────────────────
def test_llm_call_requires_cost_and_latency_client(review_id):
    """Client-side guard: emit_agent_event raises if llm.call lacks cost/latency."""
    from backend.observability.events import emit_agent_event
    from backend.models.enums import EventType

    with pytest.raises(ValueError, match="cost_usd and latency_ms"):
        emit_agent_event(
            str(review_id), "security", EventType.LLM_CALL,
            model="kimi-k3", tokens_in=100, tokens_out=50,
        )


def test_llm_call_requires_cost_and_latency_db(conn, review_id):
    """DB-side guard: the CHECK constraint rejects llm.call without cost/latency."""
    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "insert into agent_events (ts, review_id, agent, event_type) "
                    "values (now(), %s, 'security', 'llm.call')",
                    (review_id,),
                )


# ── 5. Non-LLM events may omit cost/latency ──────────────────────────────
def test_non_llm_events_may_omit_cost(conn, review_id):
    """span.start and decision rows don't need cost/latency."""
    from backend.observability.events import emit_agent_event
    from backend.models.enums import EventType, Outcome

    rid = str(review_id)
    sid = emit_agent_event(rid, "aggregator", EventType.DECISION,
                          outcome=Outcome.APPROVED, confidence=0.95, conn=conn)
    assert sid is not None

    # Verify it landed
    count = _scalars(
        conn,
        "select count(*) from agent_events "
        "where review_id = %s and event_type = 'decision'",
        (review_id,),
    )
    assert count[0] == 1


# ── 6. Full simulated review with 4 agents + aggregator ─────────────────
def test_full_simulated_review_trace(conn, review_id):
    """Simulate a complete review: orchestrator span with 4 agent children,
    each emitting span.start / llm.call / span.end, then aggregator decision.

    Assert the full trace is reconstructable from agent_events by review_id.
    """
    from backend.observability.events import emit_span, emit_agent_event, get_events_for_review
    from backend.models.enums import EventType, Outcome

    rid = str(review_id)
    agents = ["security", "quality", "tests", "docs"]

    # Root: orchestrator span
    with emit_span(rid, "orchestrator", conn=conn) as root_span:
        for agent_name in agents:
            with emit_span(rid, agent_name, model="kimi-k3", conn=conn):
                # Each agent makes one LLM call
                emit_agent_event(
                    rid, agent_name, EventType.LLM_CALL,
                    model="kimi-k3", tokens_in=500, tokens_out=200,
                    cost_usd=0.01, latency_ms=120,
                    confidence=0.85, conn=conn,
                )

        # Aggregator decision
        emit_agent_event(
            rid, "aggregator", EventType.DECISION,
            outcome=Outcome.REQUEST_CHANGES,
            confidence=Decimal("0.78"),
            payload={"findings_count": 3},
            conn=conn,
        )

    # Reconstruct the full trace
    events = get_events_for_review(rid, conn=conn)

    # Should have: 1 root span.start + 4 agent span.start + 4 llm.call
    #              + 4 agent span.end + 1 root span.end + 1 decision = 15
    assert len(events) == 15, (
        f"expected 15 events for full review, got {len(events)}"
    )

    # Every span.start should have a matching span.end with the same span_id
    start_ids = {e["span_id"] for e in events if e["event_type"] == "span.start"}
    end_ids = {e["span_id"] for e in events if e["event_type"] == "span.end"}
    assert start_ids == end_ids, (
        f"span.start IDs {start_ids} != span.end IDs {end_ids}"
    )

    # The root span should have parent_span = None
    root_events = [
        e for e in events
        if e["agent"] == "orchestrator" and e["event_type"] == "span.start"
    ]
    assert len(root_events) == 1
    assert root_events[0]["parent_span"] is None, (
        f"root span should have parent_span=None, got {root_events[0]['parent_span']}"
    )

    # All agent spans should have parent_span = root span_id
    agent_starts = [
        e for e in events
        if e["agent"] in agents and e["event_type"] == "span.start"
    ]
    assert len(agent_starts) == 4
    for start in agent_starts:
        assert start["parent_span"] == root_span, (
            f"agent {start['agent']} span parent {start['parent_span']} "
            f"!= root {root_span}"
        )

    # The decision event should be the last or near-last event
    decision = [e for e in events if e["event_type"] == "decision"]
    assert len(decision) == 1
    assert decision[0]["outcome"] == "request_changes"

    # Verify all 5 agents appear (4 specialists + orchestrator + aggregator = 6)
    agents_seen = {e["agent"] for e in events}
    assert agents_seen == {"orchestrator", "security", "quality", "tests", "docs", "aggregator"}, (
        f"unexpected agents: {agents_seen}"
    )