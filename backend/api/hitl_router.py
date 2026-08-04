"""hitl_router — human review actions (M18, Phase 19 partial).

Key-protected (same governance gate as /audit):
  POST /hitl/reviews/{review_id}/findings/{finding_id}/dispute  {reason}
  POST /hitl/reviews/{review_id}/findings/{finding_id}/feedback {helpful, note}
  GET  /hitl/reviews/{review_id}/disputes
  GET  /hitl/reviews/{review_id}/feedback
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.auth.dependencies import require_governance_key
from backend.hitl.dispute import list_disputes, record_dispute
from backend.hitl.feedback import list_feedback, record_feedback
from backend.models.enums import EventType

router = APIRouter(
    prefix="/hitl",
    tags=["hitl"],
    dependencies=[Depends(require_governance_key)],
)


@router.post("/reviews/{review_id}/findings/{finding_id}/dispute")
async def dispute(
    review_id: str, finding_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Record a human dispute of a finding (append-only event)."""
    reason = str(body.get("reason", ""))[:500]
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required")
    from backend.database.postgres import get_connection

    try:
        with get_connection() as conn:
            event_id = record_dispute(
                review_id, finding_id, reason,
                reviewer=str(body.get("reviewer", "")), conn=conn,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "recorded", "event_id": str(event_id), "event_type": EventType.DISPUTE.value}


@router.post("/reviews/{review_id}/findings/{finding_id}/feedback")
async def feedback(
    review_id: str, finding_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Record whether a human found a finding helpful."""
    from backend.database.postgres import get_connection

    try:
        with get_connection() as conn:
            event_id = record_feedback(
                review_id, finding_id,
                helpful=bool(body.get("helpful", True)),
                note=str(body.get("note", ""))[:500],
                conn=conn,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "recorded", "event_id": str(event_id), "event_type": EventType.FEEDBACK.value}


@router.get("/reviews/{review_id}/disputes")
async def disputes(review_id: str) -> dict[str, Any]:
    """All disputes recorded for a review."""
    try:
        items = list_disputes(review_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"review_id": review_id, "disputes": items, "count": len(items)}


@router.get("/reviews/{review_id}/feedback")
async def feedback_list(review_id: str) -> dict[str, Any]:
    """All feedback events for a review."""
    try:
        items = list_feedback(review_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"review_id": review_id, "feedback": items, "count": len(items)}
