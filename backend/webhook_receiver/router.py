"""router — FastAPI router for the webhook endpoint.

POST /webhook/github
  1. Verify HMAC signature (before any work)
  2. Parse the payload
  3. Check idempotency (delivery UUID)
  4. Claim the delivery (insert pr_review_records)
  5. Run the review pipeline (agents + aggregator + decide)
  6. Post the review to GitHub (with circuit breaker + retry)
  7. Return 200 fast (the worker handles the heavy lifting)

For M9 we run the pipeline synchronously in the test. In production
this would enqueue to Redis/ARQ and return 200 immediately.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.core.exceptions import (
    IdempotencyError,
    InjectionDetected,
    PrReviewError,
)
from backend.database.postgres import get_connection
from backend.observability.events import emit_agent_event
from backend.reliability.idempotency import claim_delivery, is_duplicate_delivery
from backend.webhook_receiver.parser import get_head_sha, parse_webhook
from backend.webhook_receiver.validator import verify_signature

router = APIRouter()


@router.post("/webhook/github")
async def github_webhook(request: Request) -> JSONResponse:
    """Receive a GitHub pull_request webhook and kick off a review."""
    body = await request.body()

    # 1. Verify HMAC signature
    headers_dict = {k: v for k, v in request.headers.items()}
    headers_lower = {k.lower(): v for k, v in headers_dict.items()}
    signature = headers_lower.get("x-hub-signature-256", "")

    import os

    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        return JSONResponse(
            status_code=503,
            content={"error": "webhook secret not configured"},
        )

    try:
        sig_valid = verify_signature(body, signature, secret)
    except PrReviewError as exc:
        # Missing / malformed signature header — reject before any work
        return JSONResponse(
            status_code=401,
            content={"error": str(exc)},
        )

    if not sig_valid:
        return JSONResponse(
            status_code=401,
            content={"error": "invalid signature"},
        )

    # 2. Parse
    try:
        webhook = parse_webhook(body, headers_dict)
    except PrReviewError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": str(exc)},
        )

    delivery_uuid = webhook.delivery_uuid
    repo = webhook.repository.full_name
    pr_number = webhook.pull_request.number
    head_sha = get_head_sha(webhook)

    # 3. Idempotency check
    try:
        with get_connection() as conn:
            if is_duplicate_delivery(delivery_uuid, repo, pr_number, conn=conn):
                return JSONResponse(
                    status_code=200,
                    content={"status": "duplicate", "delivery_uuid": delivery_uuid},
                )

            # 4. Claim the delivery (the durable record — source of truth)
            review_id = claim_delivery(
                delivery_uuid, repo, pr_number, head_sha, conn=conn
            )
    except IdempotencyError:
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "delivery_uuid": delivery_uuid},
        )

    # 5. Enqueue the pipeline job (M17 — production async path). Fail-soft:
    #    Redis down → still accept the claim; the review stays pending and
    #    remains runnable via POST /reviews/{id}/run. A queue failure never
    #    loses a review.
    from backend.job_queue.arq_worker import enqueue_review

    diff = getattr(webhook, "diff", "") or ""  # optional embedded diff (test payloads)
    queued = False
    job_id: str | None = None
    try:
        job_id = await enqueue_review(review_id, diff, repo, pr_number, head_sha)
        queued = True
    except ConnectionError:
        emit_agent_event(
            str(review_id), "job_queue", "tool.call",
            payload={"status": "error", "error": "redis unavailable — review enqueued in postgres only"},
        )
    except OSError:
        # Defense in depth: raw network errors that escape the normalization
        # (enqueue_review raises the builtin ConnectionError on redis-py
        # failures, but a socket error can surface as OSError directly).
        emit_agent_event(
            str(review_id), "job_queue", "tool.call",
            payload={"status": "error", "error": "queue unavailable (OSError) — review enqueued in postgres only"},
        )

    # 6. Return 202 fast (the worker handles the heavy lifting)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "review_id": str(review_id),
            "delivery_uuid": delivery_uuid,
            "queued": queued,
            "job_id": job_id,
        },
    )


def run_review_pipeline(
    review_id: UUID | str,
    diff: str,
    repo: str,
    pr_number: int,
    head_sha: str | None = None,
    *,
    github_client: Any = None,
    conn: Any = None,
) -> dict[str, Any]:
    """Run the full review pipeline and post the result to GitHub.

    This is the integration function for M9: it ties together
    orchestrator → aggregator → decide → post to GitHub.

    Returns a dict with review_id, decision, findings_count, github_review_id.
    """
    from backend.orchestrator.graph import build_graph
    from backend.orchestrator.state import OrchestratorState

    # Build and run the graph
    graph = build_graph()
    state: OrchestratorState = {
        "review_id": str(review_id),
        "repo": repo,
        "diff": diff,
        "context_chunks": [],
        "pr_number": pr_number,
        "head_sha": head_sha,
        "agent_results": [],
        "merged_findings": [],
        "overall_confidence": None,
        "decision": None,
        "errors": [],
        "model": None,
    }
    result = graph.invoke(state)

    decision = result.get("decision", "approval_queue")
    findings = result.get("merged_findings", [])
    errors = result.get("errors", [])

    # Post to GitHub (if decision is auto_post)
    github_review_id: int | None = None
    if decision == "auto_post" and github_client is not None:
        body = _format_review_body(findings)
        try:
            github_review_id = github_client.post_review(
                repo, pr_number, body, event="COMMENT", head_sha=head_sha,
            )
        except PrReviewError as exc:
            # GitHub failed — the review is queued, not lost
            emit_agent_event(
                str(review_id), "aggregator", "decision",
                outcome="escalated",
                payload={"reason": "github_post_failed", "error": str(exc)[:200]},
                conn=conn,
            )
            decision = "approval_queue"

    # ── Persist the run outcome — the DB is the source of truth ──
    from backend.database.repository import insert_finding, update_review_status
    from backend.hitl.queue import enqueue
    from backend.models.enums import EventType, Outcome

    overall_confidence = result.get("overall_confidence")

    for f in findings:
        insert_finding(review_id, f, conn=conn)

    if decision == "auto_post":
        if github_review_id is not None:
            update_review_status(
                review_id, "posted",
                overall_confidence=overall_confidence,
                github_review_id=github_review_id, conn=conn,
            )
        else:
            # No GitHub client in this run (e.g. manual /reviews/{id}/run) —
            # the review itself is finished even though nothing was posted.
            update_review_status(
                review_id, "completed",
                overall_confidence=overall_confidence, conn=conn,
            )
    elif decision == "approval_queue":
        update_review_status(
            review_id, "queued", overall_confidence=overall_confidence, conn=conn,
        )
        enqueue(review_id, "agent_failure" if errors else "low_confidence", conn=conn)
    else:  # escalate
        update_review_status(
            review_id, "escalated", overall_confidence=overall_confidence, conn=conn,
        )
        enqueue(review_id, "critical_finding", conn=conn)

    # Decision event for the trace (the aggregator's verdict)
    if decision == "escalate":
        outcome = Outcome.ESCALATED
    elif decision == "approval_queue":
        outcome = Outcome.CRITICAL_BLOCK if errors else Outcome.REQUEST_CHANGES
    else:
        outcome = Outcome.APPROVED
    emit_agent_event(
        str(review_id), "aggregator", EventType.DECISION,
        outcome=outcome,
        confidence=overall_confidence,
        payload={"decision": decision, "errors": errors[:5]},
        conn=conn,
    )

    return {
        "review_id": str(review_id),
        "decision": decision,
        "findings_count": len(findings),
        "github_review_id": github_review_id,
        "errors": errors,
    }


def _format_review_body(findings: list[dict[str, Any]]) -> str:
    """Format findings into a GitHub review comment."""
    if not findings:
        return "## AI PR Review\n\nNo issues found. Looks good!"

    lines = ["## AI PR Review\n"]
    for i, f in enumerate(findings, 1):
        severity = f.get("severity", "INFO")
        summary = f.get("summary", "")
        file_path = f.get("file_path", "?")
        line_start = f.get("line_start", "?")
        confidence = f.get("confidence", 0)
        suggestion = f.get("suggestion", "")
        rationale = f.get("rationale", "")
        agreed_by = f.get("agreed_by", [])

        lines.append(f"### {i}. [{severity}] {summary}")
        lines.append(f"**File:** `{file_path}:{line_start}`")
        lines.append(f"**Confidence:** {float(confidence):.0%}")
        if agreed_by:
            lines.append(f"**Agreed by:** {', '.join(agreed_by)}")
        if suggestion:
            lines.append(f"\n{suggestion}")
        if rationale:
            lines.append(f"\n> {rationale}")
        lines.append("")

    return "\n".join(lines)