"""main — FastAPI application entry point.

Run the server:
    uvicorn backend.main:app --reload --port 8000

Or:
    python -m backend.main

Endpoints:
    POST /webhook/github  — receive a GitHub PR webhook, kick off a review
    GET  /health           — health check
    GET  /reviews/{id}     — get a review record + findings
    GET  /hitl/queue       — list queued HITL reviews
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Load .env at import time
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

from backend.database.postgres import get_connection
from backend.database.repository import get_findings_for_review, get_review_record
from backend.hitl.queue import list_queued
from backend.observability.events import get_events_for_review
from backend.webhook_receiver.router import router as webhook_router

app = FastAPI(
    title="AI PR Review Agent",
    description="Production-grade AI PR review agent — grounded agentic fan-out",
    version="0.1.0",
)

# Mount the webhook router
app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check — verifies Tiger Cloud connectivity."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "error": str(exc)[:200]},
        )


@app.get("/reviews/{review_id}")
async def get_review(review_id: str) -> dict[str, Any]:
    """Get a review record + its findings + the events trace."""
    try:
        rid = UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid review_id UUID")

    with get_connection() as conn:
        record = get_review_record(rid, conn=conn)
        if record is None:
            raise HTTPException(status_code=404, detail="review not found")

        findings = get_findings_for_review(rid, conn=conn)
        events = get_events_for_review(str(rid), conn=conn)

    return {
        "review": {
            "id": str(record["id"]),
            "repo": record["repo"],
            "pr_number": record["pr_number"],
            "delivery_uuid": record["delivery_uuid"],
            "status": record["status"],
            "overall_confidence": float(record["overall_confidence"])
            if record["overall_confidence"]
            else None,
            "github_review_id": record["github_review_id"],
            "created_at": str(record["created_at"]) if record["created_at"] else None,
            "posted_at": str(record["posted_at"]) if record["posted_at"] else None,
        },
        "findings": [
            {
                "id": str(f["id"]),
                "agent_type": f["agent_type"],
                "severity": f["severity"],
                "category": f["category"],
                "summary": f["summary"],
                "file_path": f["file_path"],
                "line_start": f["line_start"],
                "line_end": f["line_end"],
                "suggestion": f["suggestion"],
                "confidence": float(f["confidence"]) if f["confidence"] else None,
                "rationale": f["rationale"],
            }
            for f in findings
        ],
        "events_count": len(events),
    }


@app.get("/hitl/queue")
async def get_hitl_queue() -> list[dict[str, Any]]:
    """List all queued HITL reviews."""
    with get_connection() as conn:
        entries = list_queued(conn=conn)
    return [
        {
            "id": str(e["id"]),
            "review_id": str(e["review_id"]),
            "reason": e["reason"],
            "state": e["state"],
            "created_at": str(e["created_at"]) if e["created_at"] else None,
        }
        for e in entries
    ]


@app.post("/reviews/{review_id}/run")
async def run_review(review_id: str, diff: str = "") -> dict[str, Any]:
    """Manually trigger the review pipeline for an existing review_id.

    This is the manual entry point: you create a review via webhook (or
    manually), then call this endpoint to run the agent fan-out.

    Query param: ?diff=<the PR diff text>
    """
    from backend.webhook_receiver.router import run_review_pipeline

    try:
        rid = UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid review_id UUID")

    with get_connection() as conn:
        record = get_review_record(rid, conn=conn)
        if record is None:
            raise HTTPException(status_code=404, detail="review not found")

        result = run_review_pipeline(
            review_id=rid,
            diff=diff,
            repo=record["repo"],
            pr_number=record["pr_number"],
            head_sha=record["head_sha"],
            conn=conn,
        )

    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )