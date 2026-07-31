"""cost_repository — read cost data from agent_events and agent_health_1m.

Provides per-agent and per-review cost aggregation queries.
The continuous aggregate agent_health_1m is the production read path;
the raw agent_events sum is used when the aggregate may lag.
"""

from __future__ import annotations

from typing import Any


def get_total_cost_for_review(review_id: str, *, conn: Any = None) -> float:
    """Sum all cost_usd for a single review_id from agent_events."""
    from backend.database.postgres import get_connection

    sql = """
        SELECT COALESCE(sum(cost_usd), 0)
        FROM agent_events
        WHERE review_id = %s AND cost_usd IS NOT NULL
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (review_id,))
            row = cur.fetchone()
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (review_id,))
                row = cur.fetchone()
    return float(row[0]) if row else 0.0


def get_agent_cost_from_aggregate(agent: str, *, conn: Any = None) -> float:
    """Read today's cost for an agent from the agent_health_1m aggregate.

    The continuous aggregate may lag by up to a minute (refresh policy).
    Use get_agent_daily_spend() from budget.py for a real-time read.
    """
    from backend.database.postgres import get_connection

    sql = """
        SELECT COALESCE(sum(cost_usd), 0)
        FROM agent_health_1m
        WHERE bucket >= date_trunc('day', now())
          AND agent = %s
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (agent,))
            row = cur.fetchone()
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (agent,))
                row = cur.fetchone()
    return float(row[0]) if row else 0.0


def get_agent_cost_from_raw(agent: str, *, conn: Any = None) -> float:
    """Sum cost_usd from raw agent_events for an agent today."""
    from backend.database.postgres import get_connection

    sql = """
        SELECT COALESCE(sum(cost_usd), 0)
        FROM agent_events
        WHERE ts >= date_trunc('day', now())
          AND agent = %s
          AND cost_usd IS NOT NULL
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (agent,))
            row = cur.fetchone()
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (agent,))
                row = cur.fetchone()
    return float(row[0]) if row else 0.0