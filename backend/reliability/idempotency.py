"""idempotency — webhook delivery deduplication via delivery UUID.

X-GitHub-Delivery is the idempotency key. The pr_review_records table
has a UNIQUE constraint on (repo, pr_number, delivery_uuid) — inserting
a second row for the same key fails, preventing double-processing.

This module provides the application-level check + the DB-level guarantee.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from backend.core.exceptions import IdempotencyError
from backend.database.postgres import get_connection


def is_duplicate_delivery(
    delivery_uuid: str,
    repo: str,
    pr_number: int,
    *,
    conn: Any = None,
) -> bool:
    """Check if this delivery UUID has already been processed.

    True if a pr_review_records row exists for this (repo, pr_number, delivery_uuid).
    """
    sql = """
        SELECT 1 FROM pr_review_records
        WHERE delivery_uuid = %s AND repo = %s AND pr_number = %s
        LIMIT 1
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (delivery_uuid, repo, pr_number))
            return cur.fetchone() is not None
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (delivery_uuid, repo, pr_number))
                return cur.fetchone() is not None


def claim_delivery(
    delivery_uuid: str,
    repo: str,
    pr_number: int,
    head_sha: str | None = None,
    *,
    conn: Any = None,
) -> UUID:
    """Insert a pr_review_records row to claim this delivery.

    If the (repo, pr_number, delivery_uuid) already exists, raises
    IdempotencyError — this is the DB-level idempotency guarantee.

    Returns the review_id (the new row's UUID).
    """
    import uuid

    review_id = uuid.uuid4()
    sql = """
        INSERT INTO pr_review_records (id, repo, pr_number, delivery_uuid, head_sha)
        VALUES (%s, %s, %s, %s, %s)
    """
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (str(review_id), repo, pr_number, delivery_uuid, head_sha))
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise IdempotencyError(
                    f"duplicate delivery: {delivery_uuid} for {repo}#{pr_number}"
                ) from exc
            raise
    else:
        try:
            with get_connection() as c:
                with c.cursor() as cur:
                    cur.execute(sql, (str(review_id), repo, pr_number, delivery_uuid, head_sha))
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise IdempotencyError(
                    f"duplicate delivery: {delivery_uuid} for {repo}#{pr_number}"
                ) from exc
            raise

    return review_id