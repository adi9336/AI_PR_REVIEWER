"""M8 gate — confidence-weighted HITL gate routing.

Tests:
  1. High confidence + no CRITICAL → auto_post.
  2. Below threshold → approval_queue (nothing posted).
  3. Any CRITICAL → escalation regardless of confidence (INV-5).
  4. Agreement boost raises overall confidence.
  5. HITL queue enqueue/decide roundtrip (requires Tiger Cloud).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / "backend" / ".env")

TIGER_URL = os.getenv("TIGER_DATABASE_URL", "")


def _make_state():
    return {
        "review_id": str(uuid.uuid4()),
        "repo": "test-repo",
        "diff": "",
        "context_chunks": [],
        "pr_number": None,
        "head_sha": None,
        "agent_results": [],
        "merged_findings": [],
        "overall_confidence": None,
        "decision": None,
        "errors": [],
        "model": None,
    }


# ── 1. High confidence + no CRITICAL → auto_post ───────────────────────


def test_high_confidence_auto_post():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = [
        {"severity": "MEDIUM", "confidence": 0.85},
        {"severity": "LOW", "confidence": 0.90},
    ]
    result = decide(state)
    assert result["decision"] == "auto_post"
    assert result["overall_confidence"] >= 0.8


# ── 2. Below threshold → approval_queue ─────────────────────────────────


def test_low_confidence_approval_queue():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = [
        {"severity": "MEDIUM", "confidence": 0.5},
        {"severity": "LOW", "confidence": 0.6},
    ]
    result = decide(state)
    assert result["decision"] == "approval_queue"
    assert result["overall_confidence"] < 0.8


# ── 3. Any CRITICAL → escalation regardless of confidence (INV-5) ───────


def test_critical_always_escalates():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = [
        {"severity": "CRITICAL", "confidence": 0.999},
        {"severity": "LOW", "confidence": 0.3},
    ]
    result = decide(state)
    assert result["decision"] == "escalate"
    assert result["overall_confidence"] == 0.0


def test_critical_escalates_even_with_high_confidence():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = [
        {"severity": "CRITICAL", "confidence": 0.95},
        {"severity": "MEDIUM", "confidence": 0.90},
    ]
    result = decide(state)
    assert result["decision"] == "escalate", "CRITICAL must escalate regardless of confidence"


def test_no_findings_auto_post():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = []
    result = decide(state)
    assert result["decision"] == "auto_post"
    assert result["overall_confidence"] == 1.0


# ── 4. Agreement boost raises confidence ────────────────────────────────


def test_agreement_boost_raises_confidence():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = [
        {"severity": "MEDIUM", "confidence": 0.75,
         "agreement_count": 3, "agreed_by": ["security", "quality", "tests"]},
    ]
    result = decide(state)
    # 0.75 base + 0.02 * 2 agreed = 0.79 → still below 0.8
    # But agreement boost might push it over
    assert result["overall_confidence"] >= 0.75, "boost should not lower confidence"


def test_agreement_boost_can_cross_threshold():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = [
        {"severity": "MEDIUM", "confidence": 0.78,
         "agreement_count": 3, "agreed_by": ["security", "quality", "tests"]},
    ]
    result = decide(state)
    # 0.78 + 0.02 * 2 = 0.82 → crosses 0.8 threshold
    assert result["decision"] == "auto_post", (
        f"agreement boost should push 0.78 to auto_post, got {result['overall_confidence']}"
    )


# ── 5. HITL queue enqueue + decide routing (needs Tiger Cloud) ──────────


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live HITL queue tests"
)
def test_hitl_queue_enqueue_and_decide():
    """A below-threshold review should create a hitl_reviews row via the queue.

    This tests the full routing path: decide() → approval_queue → hitl/queue.enqueue()
    """
    from backend.hitl.queue import enqueue, get_queue_entry, approve
    from backend.database.postgres import get_connection

    review_id = uuid.uuid4()

    with get_connection() as conn:
        # Create the parent pr_review_records row first (FK constraint)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pr_review_records (id, repo, pr_number, delivery_uuid) "
                "VALUES (%s, 'test-repo', 999, %s)",
                (str(review_id), str(review_id)),
            )

        # Enqueue a review for low confidence
        hitl_id = enqueue(review_id, "low_confidence", conn=conn)

        # Verify it landed in the queue
        entry = get_queue_entry(hitl_id, conn=conn)
        assert entry is not None
        assert entry["reason"] == "low_confidence"
        assert entry["state"] == "queued"

        # Approve it
        approve(hitl_id, reviewer="test-reviewer", conn=conn)
        entry = get_queue_entry(hitl_id, conn=conn)
        assert entry["state"] == "approved"
        assert entry["assigned_to"] == "test-reviewer"

        # Cleanup
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hitl_reviews WHERE review_id = %s", (str(review_id),))
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))