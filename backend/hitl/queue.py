"""queue — HITL review queue CRUD.

When a review lands below the confidence threshold or has a CRITICAL
finding, a row is inserted into hitl_reviews. A human reviewer then
approves or rejects it. This module provides the data-access layer.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from backend.database.postgres import get_connection


def enqueue(
    review_id: UUID | str,
    reason: str,
    *,
    conn: Any = None,
) -> UUID:
    """Insert a row into hitl_reviews and return its id.

    reason: low_confidence | critical_finding | dispute
    """
    rid = uuid.UUID(str(review_id))
    hid = uuid.uuid4()
    sql = """
        INSERT INTO hitl_reviews (id, review_id, reason, state)
        VALUES (%s, %s, %s, 'queued')
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (str(hid), rid, reason))
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (str(hid), rid, reason))
    return hid


def get_queue_entry(
    hitl_id: UUID | str,
    *,
    conn: Any = None,
) -> dict[str, Any] | None:
    """Fetch a single HITL queue entry by id."""
    sql = """
        SELECT id, review_id, reason, state, assigned_to, decided_at, created_at
        FROM hitl_reviews WHERE id = %s
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (uuid.UUID(str(hitl_id)),))
            row = cur.fetchone()
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (uuid.UUID(str(hitl_id)),))
                row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "review_id": row[1],
        "reason": row[2],
        "state": row[3],
        "assigned_to": row[4],
        "decided_at": row[5],
        "created_at": row[6],
    }


def list_queued(
    *,
    conn: Any = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List all queued HITL reviews (oldest first)."""
    sql = """
        SELECT id, review_id, reason, state, created_at
        FROM hitl_reviews
        WHERE state = 'queued'
        ORDER BY created_at ASC
        LIMIT %s
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (limit,))
                rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "review_id": r[1],
            "reason": r[2],
            "state": r[3],
            "created_at": r[4],
        }
        for r in rows
    ]


def approve(
    hitl_id: UUID | str,
    *,
    reviewer: str | None = None,
    conn: Any = None,
) -> None:
    """Mark a HITL review as approved."""
    sql = """
        UPDATE hitl_reviews
        SET state = 'approved', decided_at = now(), assigned_to = COALESCE(%s, assigned_to)
        WHERE id = %s AND state = 'queued'
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (reviewer, uuid.UUID(str(hitl_id))))
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (reviewer, uuid.UUID(str(hitl_id))))


def reject(
    hitl_id: UUID | str,
    *,
    reviewer: str | None = None,
    conn: Any = None,
) -> None:
    """Mark a HITL review as rejected."""
    sql = """
        UPDATE hitl_reviews
        SET state = 'rejected', decided_at = now(), assigned_to = COALESCE(%s, assigned_to)
        WHERE id = %s AND state = 'queued'
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (reviewer, uuid.UUID(str(hitl_id))))
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (reviewer, uuid.UUID(str(hitl_id))))