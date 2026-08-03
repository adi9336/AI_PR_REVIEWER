"""repository — CRUD for pr_review_records and finding_records.

Provides typed data access for the truth lane tables.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

from backend.database.postgres import get_connection


def create_review_record(
    review_id: UUID | str,
    repo: str,
    pr_number: int,
    delivery_uuid: str,
    head_sha: str | None = None,
    *,
    conn: Any = None,
) -> None:
    """Insert a row into pr_review_records."""
    rid = str(review_id) if isinstance(review_id, UUID) else review_id
    sql = """
        INSERT INTO pr_review_records (id, repo, pr_number, delivery_uuid, head_sha)
        VALUES (%s, %s, %s, %s, %s)
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (rid, repo, pr_number, delivery_uuid, head_sha))
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (rid, repo, pr_number, delivery_uuid, head_sha))


def update_review_status(
    review_id: UUID | str,
    status: str,
    *,
    overall_confidence: float | None = None,
    github_review_id: int | None = None,
    conn: Any = None,
) -> None:
    """Update a review record's status."""
    rid = uuid.UUID(str(review_id))
    parts = ["status = %s"]
    params: list[Any] = [status]
    if overall_confidence is not None:
        parts.append("overall_confidence = %s")
        params.append(Decimal(str(overall_confidence)))
    if github_review_id is not None:
        parts.append("github_review_id = %s")
        params.append(github_review_id)
    if status == "posted":
        parts.append("posted_at = now()")
    params.append(rid)
    sql = f"UPDATE pr_review_records SET {', '.join(parts)} WHERE id = %s"
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, params)


def get_review_record(review_id: UUID | str, *, conn: Any = None) -> dict[str, Any] | None:
    """Fetch a review record by id."""
    rid = uuid.UUID(str(review_id))
    sql = """
        SELECT id, repo, pr_number, delivery_uuid, head_sha,
               overall_confidence, status, github_review_id, created_at, posted_at
        FROM pr_review_records WHERE id = %s
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (rid,))
            row = cur.fetchone()
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (rid,))
                row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "repo": row[1], "pr_number": row[2],
        "delivery_uuid": row[3], "head_sha": row[4],
        "overall_confidence": row[5], "status": row[6],
        "github_review_id": row[7], "created_at": row[8], "posted_at": row[9],
    }


def insert_finding(
    review_id: UUID | str,
    finding: dict[str, Any],
    *,
    conn: Any = None,
) -> UUID:
    """Insert a finding_record row."""
    rid = uuid.UUID(str(review_id))
    fid = uuid.uuid4()
    confidence = finding.get("confidence")
    sql = """
        INSERT INTO finding_records
            (id, review_id, agent_type, severity, category, summary,
             file_path, line_start, line_end, suggestion, confidence, rationale)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    params = (
        str(fid), rid,
        finding.get("agent_type", "security"),
        finding.get("severity", "INFO"),
        finding.get("category"),
        finding.get("summary", ""),
        finding.get("file_path"),
        finding.get("line_start"),
        finding.get("line_end"),
        finding.get("suggestion"),
        Decimal(str(confidence)) if confidence is not None else None,
        finding.get("rationale"),
    )
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, params)
    return fid


def get_findings_for_review(review_id: UUID | str, *, conn: Any = None) -> list[dict[str, Any]]:
    """Fetch all finding_records for a review."""
    rid = uuid.UUID(str(review_id))
    sql = """
        SELECT id, review_id, agent_type, severity, category, summary,
               file_path, line_start, line_end, suggestion, confidence, rationale
        FROM finding_records WHERE review_id = %s ORDER BY severity, id
    """
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (rid,))
            rows = cur.fetchall()
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(sql, (rid,))
                rows = cur.fetchall()
    return [
        {
            "id": r[0], "review_id": r[1], "agent_type": r[2],
            "severity": r[3], "category": r[4], "summary": r[5],
            "file_path": r[6], "line_start": r[7], "line_end": r[8],
            "suggestion": r[9], "confidence": r[10], "rationale": r[11],
        }
        for r in rows
    ]


def list_reviews(*, limit: int = 50, conn: Any = None) -> list[dict[str, Any]]:
    """Recent reviews, newest first — the dashboard home feed (M15)."""

    def _run(cursor: Any) -> list[dict[str, Any]]:
        cursor.execute(
            "SELECT id, repo, pr_number, status, overall_confidence, created_at "
            "FROM pr_review_records ORDER BY created_at DESC LIMIT %s",
            (max(1, min(int(limit), 200)),),
        )
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    if conn is not None:
        with conn.cursor() as cur:
            return _run(cur)
    with get_connection() as c:
        with c.cursor() as cur:
            return _run(cur)