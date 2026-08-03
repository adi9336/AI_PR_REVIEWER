"""audit — governance API: queryable audit + explainability (M14, Phase 15).

All routes are protected by require_governance_key (fail-closed API key):
  GET /audit/events                     — query the audit spine
  GET /audit/reviews/{id}/summary       — per-review rollup
  GET /audit/reviews/{id}/explain/{fid} — why a finding exists
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.auth.dependencies import require_governance_key
from backend.observability.audit import audit_summary, explain_finding, query_audit

router = APIRouter(
    prefix="/audit",
    tags=["governance"],
    dependencies=[Depends(require_governance_key)],
)


@router.get("/events")
async def events(
    agent: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query the audit spine (time-ordered, secret-masked)."""
    return query_audit(agent=agent, event_type=event_type, limit=limit)


@router.get("/reviews/{review_id}/summary")
async def summary(review_id: str) -> dict[str, Any]:
    """Per-review audit rollup: counts, agents, LLM calls, cost."""
    try:
        return audit_summary(review_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/drift")
async def drift(
    window_days: int = 7,
    baseline_days: int = 7,
    threshold_pct: float = 20.0,
) -> dict[str, Any]:
    """Continuous-learning drift report (Phase 20)."""
    from backend.observability.drift import detect_drift

    try:
        report = detect_drift(
            window_days=window_days,
            baseline_days=baseline_days,
            threshold_pct=threshold_pct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return report.as_dict()


@router.get("/reviews/{review_id}/trace")
async def trace(review_id: str) -> dict[str, Any]:
    """Full time-ordered events trace for a review (Phase 17 DX view)."""
    try:
        events = query_audit(review_id=review_id, limit=1000)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"review_id": review_id, "events": events, "count": len(events)}


@router.get("/reviews/{review_id}/explain/{finding_id}")
async def explain(review_id: str, finding_id: str) -> dict[str, Any]:
    """Reconstruct why a finding exists (finding + trace + prompt versions)."""
    try:
        return explain_finding(review_id, finding_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
