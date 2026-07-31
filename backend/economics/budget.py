"""budget — BudgetGuard: hard-blocks LLM calls past the daily cap (ADR-004).

Before any LLM call, the agent reads the day's running cost from the
agent_health_1m continuous aggregate. If the daily total exceeds the
configured cap, BudgetExceeded is raised and ZERO LLM calls are made.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any

from backend.core.exceptions import BudgetExceeded
from backend.database.postgres import get_connection

logger = logging.getLogger(__name__)

# Default daily cap: $10 (can be overridden via DAILY_COST_CAP_USD env var)
DEFAULT_DAILY_CAP = 10.0


def get_daily_cap() -> float:
    """Read the daily cost cap from env or default."""
    return float(os.getenv("DAILY_COST_CAP_USD", str(DEFAULT_DAILY_CAP)))


def get_daily_spend(*, conn: Any = None) -> float:
    """Read the total cost from agent_events for the current day.

    Reads from raw agent_events (the continuous aggregate agent_health_1m
    is populated async by TimescaleDB and may lag by a minute; the raw
    sum is always current).
    """
    sql = """
        SELECT COALESCE(sum(cost_usd), 0)
        FROM agent_events
        WHERE ts >= date_trunc('day', now())
          AND cost_usd IS NOT NULL
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
    return float(row[0]) if row else 0.0


def get_agent_daily_spend(agent: str, *, conn: Any = None) -> float:
    """Read the total cost for a specific agent today."""
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


def check_budget(*, conn: Any = None, cap: float | None = None) -> None:
    """Check if the daily budget is exceeded.

    Raises BudgetExceeded if current daily spend >= cap.
    Does nothing (returns None) if within budget.
    """
    daily_cap = cap if cap is not None else get_daily_cap()
    spent = get_daily_spend(conn=conn)
    if spent >= daily_cap:
        raise BudgetExceeded(
            f"daily cost cap exceeded: spent ${spent:.4f} >= cap ${daily_cap:.4f}"
        )


def would_exceed_budget(
    estimated_cost: float = 0.0,
    *,
    conn: Any = None,
    cap: float | None = None,
) -> bool:
    """Check if making a call with the estimated cost would exceed the budget.

    Returns True if spent + estimated_cost >= cap.
    Does NOT raise — caller decides what to do.
    """
    daily_cap = cap if cap is not None else get_daily_cap()
    spent = get_daily_spend(conn=conn)
    return (spent + estimated_cost) >= daily_cap


class BudgetGuard:
    """Context manager that checks the budget before allowing LLM calls.

    Usage:
        with BudgetGuard(conn=conn, cap=10.0):
            # LLM calls happen here
            ...
        # If the cap is exceeded, BudgetExceeded is raised before any call

    For a per-call check, use check_budget() directly.
    """

    def __init__(
        self,
        *,
        conn: Any = None,
        cap: float | None = None,
    ) -> None:
        self._conn = conn
        self._cap = cap
        self._checked = False

    def __enter__(self) -> BudgetGuard:
        check_budget(conn=self._conn, cap=self._cap)
        self._checked = True
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def assert_within_budget(self) -> None:
        """Re-check the budget (for mid-run checks)."""
        check_budget(conn=self._conn, cap=self._cap)