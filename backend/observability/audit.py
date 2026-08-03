"""audit — queryable audit log over the append-only events spine (M14).

Reads only. agent_events is immutable by construction (INV-6), so every
audit query is inherently tamper-proof; payloads are secret-masked at the
read boundary (masking.py) before anything reaches a human.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.database.postgres import get_connection
from backend.database.repository import get_findings_for_review, get_review_record
from backend.observability.events import get_events_for_review
from backend.security.masking import mask_payload

_AUDIT_COLUMNS = (
    "ts, review_id, agent, event_type, model, tokens_in, tokens_out, "
    "cost_usd, latency_ms, outcome, confidence, payload"
)


def _row(columns: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "ts": columns[0],
        "review_id": str(columns[1]),
        "agent": columns[2],
        "event_type": columns[3],
        "model": columns[4],
        "tokens_in": columns[5],
        "tokens_out": columns[6],
        "cost_usd": float(columns[7]) if columns[7] is not None else None,
        "latency_ms": columns[8],
        "outcome": columns[9],
        "confidence": float(columns[10]) if columns[10] is not None else None,
        "payload": mask_payload(dict(columns[11])) if columns[11] else None,
    }


def query_audit(
    *,
    review_id: UUID | str | None = None,
    agent: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
    conn: Any = None,
) -> list[dict[str, Any]]:
    """Query the audit spine: time-ordered, filtered, secret-masked rows."""
    where: list[str] = []
    params: list[Any] = []
    if review_id is not None:
        where.append("review_id = %s")
        params.append(uuid.UUID(str(review_id)))
    if agent is not None:
        where.append("agent = %s")
        params.append(agent)
    if event_type is not None:
        where.append("event_type = %s")
        params.append(event_type)
    if since is not None:
        where.append("ts >= %s")
        params.append(since)

    sql = f"SELECT {_AUDIT_COLUMNS} FROM agent_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts ASC LIMIT %s"
    params.append(max(1, min(int(limit), 1000)))

    def _run(cursor: Any) -> list[dict[str, Any]]:
        cursor.execute(sql, params)
        return [_row(row) for row in cursor.fetchall()]

    if conn is not None:
        with conn.cursor() as cur:
            return _run(cur)
    with get_connection() as c:
        with c.cursor() as cur:
            return _run(cur)


def audit_summary(review_id: UUID | str, *, conn: Any = None) -> dict[str, Any]:
    """Per-review rollup: event counts, agents, LLM calls, total cost."""
    events = get_events_for_review(review_id, conn=conn)
    by_type: dict[str, int] = {}
    by_agent: dict[str, int] = {}
    llm_calls = 0
    total_cost = 0.0
    for e in events:
        et = str(e["event_type"])
        by_type[et] = by_type.get(et, 0) + 1
        ag = str(e["agent"])
        by_agent[ag] = by_agent.get(ag, 0) + 1
        if et == "llm.call":
            llm_calls += 1
            total_cost += float(e.get("cost_usd") or 0.0)
    return {
        "review_id": str(review_id),
        "events": len(events),
        "by_event_type": by_type,
        "by_agent": by_agent,
        "llm_calls": llm_calls,
        "total_cost_usd": round(total_cost, 6),
    }


def explain_finding(
    review_id: UUID | str,
    finding_id: UUID | str,
    *,
    conn: Any = None,
) -> dict[str, Any]:
    """Reconstruct WHY a finding exists — the governance answer for disputes.

    Returns the finding, its review, the time-ordered events trace, the
    prompt_version(s) that produced it, and the decision events.
    """
    rid = uuid.UUID(str(review_id))
    record = get_review_record(rid, conn=conn)
    if record is None:
        raise KeyError(f"review not found: {rid}")

    findings = get_findings_for_review(rid, conn=conn)
    finding = next(
        (f for f in findings if str(f["id"]) == str(finding_id)), None
    )
    if finding is None:
        raise KeyError(f"finding {finding_id} not found in review {rid}")

    events = get_events_for_review(str(rid), conn=conn)
    prompt_versions = sorted(
        {
            str(e["payload"]["prompt_version"])
            for e in events
            if e.get("payload") and e["payload"].get("prompt_version")
        }
    )
    trace = [mask_payload(dict(e)) for e in events]
    decision_events = [
        mask_payload(dict(e))
        for e in events
        if str(e["event_type"]) == "decision"
    ]

    return {
        "finding": mask_payload(dict(finding)),
        "review": {
            "id": str(record["id"]),
            "repo": record["repo"],
            "pr_number": record["pr_number"],
            "status": record["status"],
            "overall_confidence": (
                float(record["overall_confidence"])
                if record["overall_confidence"] is not None
                else None
            ),
        },
        "prompt_versions": prompt_versions,
        "decision_events": decision_events,
        "trace": trace,
    }
