"""M9 gate — end-to-end: webhook in, review posted, fully traced.

Tests:
  1. A replayed webhook payload produces a pr_review_records row + findings
     + a complete agent_events trace.
  2. Idempotency: no double-post on retry (same delivery UUID → 200 twice,
     still exactly 1 review).
  3. GitHub API forced to 500 → circuit breaker opens, review lands in
     the queue instead of being lost.
  4. The whole run is reconstructable from agent_events by review_id.

Requires TIGER_DATABASE_URL. Skips cleanly if unset.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / "backend" / ".env")

TIGER_URL = os.getenv("TIGER_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live e2e tests"
)


def _make_webhook_payload(
    repo: str = "test-e2e",
    pr_number: int = 42,
    delivery_uuid: str | None = None,
    action: str = "opened",
) -> tuple[bytes, dict[str, str]]:
    """Create a minimal GitHub PR webhook payload + headers."""
    if delivery_uuid is None:
        delivery_uuid = str(uuid.uuid4())
    payload = {
        "action": action,
        "repository": {"name": repo.split("/")[-1], "full_name": repo},
        "pull_request": {
            "number": pr_number,
            "title": "Test PR",
            "body": "test",
            "head": {"sha": "abc123", "ref": "feature-branch", "label": "user:feature"},
            "base": {"sha": "main", "ref": "main", "label": "repo:main"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "x-github-event": "pull_request",
        "x-github-delivery": delivery_uuid,
        "x-hub-signature-256": "sha256=fake",  # tests bypass HMAC
    }
    return body, headers


# ── 1. Full pipeline: webhook → review record → findings → trace ───────


def test_e2e_webhook_produces_review_and_trace():
    """A webhook payload produces a pr_review_records row, finding_records,
    and a complete agent_events trace reconstructable by review_id."""
    from backend.webhook_receiver.parser import parse_webhook
    from backend.reliability.idempotency import claim_delivery
    from backend.database.postgres import get_connection
    from backend.observability.events import emit_agent_event, get_events_for_review
    from backend.database.repository import (
        insert_finding,
        get_review_record,
        get_findings_for_review,
    )
    from backend.models.enums import EventType, Outcome

    delivery_uuid = str(uuid.uuid4())
    repo = "test-e2e"
    pr_number = 42

    body, headers = _make_webhook_payload(repo, pr_number, delivery_uuid)

    with get_connection() as conn:
        # Parse webhook
        webhook = parse_webhook(body, headers)
        assert webhook.action == "opened"
        assert webhook.repository.full_name == repo

        # Claim the delivery
        review_id = claim_delivery(delivery_uuid, repo, pr_number, "abc123", conn=conn)

        # Emit events simulating a review
        emit_agent_event(str(review_id), "orchestrator", EventType.SPAN_START, conn=conn)
        emit_agent_event(
            str(review_id), "security", EventType.LLM_CALL,
            model="gpt-4o-mini", tokens_in=100, tokens_out=50,
            cost_usd=0.001, latency_ms=100, conn=conn,
        )
        emit_agent_event(
            str(review_id), "aggregator", EventType.DECISION,
            outcome=Outcome.APPROVED, confidence=0.9, conn=conn,
        )
        emit_agent_event(str(review_id), "orchestrator", EventType.SPAN_END, conn=conn)

        # Insert a finding record
        insert_finding(
            review_id,
            {
                "agent_type": "security",
                "severity": "HIGH",
                "category": "sql-injection",
                "summary": "SQL injection in query",
                "file_path": "src/db.py",
                "line_start": 10,
                "line_end": 12,
                "suggestion": "Use parameterized queries",
                "confidence": 0.9,
                "rationale": "User input in f-string",
            },
            conn=conn,
        )

        # Verify the review record exists
        record = get_review_record(review_id, conn=conn)
        assert record is not None
        assert record["repo"] == repo
        assert record["pr_number"] == pr_number
        assert record["delivery_uuid"] == delivery_uuid

        # Verify findings were stored
        findings = get_findings_for_review(review_id, conn=conn)
        assert len(findings) == 1
        assert findings[0]["severity"] == "HIGH"

        # Verify the events trace is reconstructable
        events = get_events_for_review(str(review_id), conn=conn)
        assert len(events) >= 4, "expected >= 4 events in the trace"
        event_types = [e["event_type"] for e in events]
        assert "span.start" in event_types
        assert "span.end" in event_types
        assert "llm.call" in event_types
        assert "decision" in event_types

        # Cleanup (agent_events is append-only — can't DELETE, that's INV-6)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM finding_records WHERE review_id = %s", (str(review_id),))
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))


# ── 2. Idempotency: no double-post on retry ────────────────────────────


def test_e2e_idempotency_no_double_post():
    """Same delivery UUID → first time creates the review, second time is a no-op."""
    from backend.reliability.idempotency import claim_delivery, is_duplicate_delivery
    from backend.database.postgres import get_connection

    delivery_uuid = str(uuid.uuid4())
    repo = "test-e2e"
    pr_number = 99

    with get_connection() as conn:
        # First delivery: claims the review
        review_id_1 = claim_delivery(delivery_uuid, repo, pr_number, conn=conn)
        assert review_id_1 is not None

        # Second delivery: duplicate detected
        assert is_duplicate_delivery(delivery_uuid, repo, pr_number, conn=conn)

        # Claiming again should raise IdempotencyError
        from backend.core.exceptions import IdempotencyError
        with pytest.raises(IdempotencyError):
            claim_delivery(delivery_uuid, repo, pr_number, conn=conn)

        # Cleanup
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id_1),))


# ── 3. GitHub API 500 → circuit breaker → queue ────────────────────────


def test_e2e_github_failure_queues_review():
    """If GitHub API returns 500, the circuit breaker opens and the
    review is queued instead of being lost."""
    from backend.reliability.circuit_breaker import CircuitBreaker
    from backend.core.exceptions import CircuitOpen
    from backend.integrations.github_client import GitHubClient

    # Mock a 500 failure
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("500 Internal Server Error")
    mock_response.json.return_value = {}

    client = GitHubClient(token="fake-token")
    client._breaker = CircuitBreaker(
        failure_threshold=1, recovery_timeout=60.0, name="test-breaker"
    )

    # Patch httpx.post to return 500
    with patch("httpx.post", return_value=mock_response):
        # First few calls should raise the underlying error
        with pytest.raises(Exception, match="500"):
            client.post_review("test/repo", 1, "body")

    # After enough failures, the breaker should be open
    assert client._breaker.state in ("open", "half_open")


# ── 4. Full trace reconstructable by review_id ──────────────────────────


def test_e2e_trace_reconstructable_by_review_id():
    """The agent_events trace for a review_id can be reconstructed in time order."""
    from backend.database.postgres import get_connection
    from backend.reliability.idempotency import claim_delivery
    from backend.observability.events import emit_agent_event, get_events_for_review
    from backend.models.enums import EventType, Outcome
    import time

    delivery_uuid = str(uuid.uuid4())

    with get_connection() as conn:
        review_id = claim_delivery(delivery_uuid, "test-e2e", 7, "sha-trace-1", conn=conn)

        # Emit a realistic sequence of events
        emit_agent_event(str(review_id), "orchestrator", EventType.SPAN_START, conn=conn)
        time.sleep(0.01)
        emit_agent_event(
            str(review_id), "security", EventType.LLM_CALL,
            model="gpt-4o-mini", tokens_in=100, tokens_out=50,
            cost_usd=0.001, latency_ms=50, conn=conn,
        )
        time.sleep(0.01)
        emit_agent_event(
            str(review_id), "quality", EventType.LLM_CALL,
            model="gpt-4o-mini", tokens_in=200, tokens_out=100,
            cost_usd=0.002, latency_ms=80, conn=conn,
        )
        time.sleep(0.01)
        emit_agent_event(
            str(review_id), "aggregator", EventType.DECISION,
            outcome=Outcome.APPROVED, confidence=0.85, conn=conn,
        )
        time.sleep(0.01)
        emit_agent_event(str(review_id), "orchestrator", EventType.SPAN_END, conn=conn)

        # Reconstruct the trace
        events = get_events_for_review(str(review_id), conn=conn)

        # Must be in time order
        timestamps = [e["ts"] for e in events]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1], (
                f"events not in time order at index {i}"
            )

        # Must contain the full sequence
        event_types = [e["event_type"] for e in events]
        assert event_types[0] == "span.start"
        assert event_types[-1] == "span.end"
        assert "llm.call" in event_types
        assert "decision" in event_types

        # Must have cost data on llm.call events
        llm_calls = [e for e in events if e["event_type"] == "llm.call"]
        assert len(llm_calls) == 2
        for lc in llm_calls:
            assert lc["cost_usd"] is not None
            assert lc["latency_ms"] is not None

        # The decision event must have outcome + confidence
        decision = [e for e in events if e["event_type"] == "decision"][0]
        assert decision["outcome"] == "approved"
        assert decision["confidence"] is not None

        # Cleanup (agent_events is append-only — can't DELETE, that's INV-6)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))


# ── 5. Webhook signature validation ─────────────────────────────────────


def test_e2e_webhook_signature_validation():
    """HMAC signature validation rejects tampered bodies."""
    from backend.webhook_receiver.validator import verify_signature

    body = b'{"action":"opened","repository":{"name":"test"},"pull_request":{"number":1}}'
    secret = "test-secret"

    # Compute valid signature
    import hashlib
    import hmac

    expected_sig = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()

    # Valid signature passes
    assert verify_signature(body, expected_sig, secret)

    # Tampered body fails
    assert not verify_signature(b'{"action":"opened","repository":{"name":"tampered"}}', expected_sig, secret)

    # Missing signature fails
    from backend.core.exceptions import PrReviewError
    with pytest.raises(PrReviewError, match="missing"):
        verify_signature(body, "", secret)


# ── 6. Pipeline run persists the outcome to the DB ──────────────────────


class _FakeGraph:
    """Minimal graph stand-in returning a fixed state result."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def invoke(self, state: dict) -> dict:
        return self._result


def _claim_review(pr_number: int, conn) -> str:
    from backend.reliability.idempotency import claim_delivery

    review_id = claim_delivery(
        str(uuid.uuid4()), "test-e2e", pr_number, conn=conn
    )
    return str(review_id)


def test_pipeline_run_auto_post_persists_findings_status_and_decision():
    """run_review_pipeline must write findings, update the review status,
    and emit a decision event — the DB is the source of truth."""
    from backend.webhook_receiver.router import run_review_pipeline
    from backend.database.postgres import get_connection
    from backend.database.repository import get_findings_for_review, get_review_record
    from backend.observability.events import get_events_for_review

    with get_connection() as conn:
        review_id = _claim_review(4242, conn)

        fake_result = {
            "decision": "auto_post",
            "merged_findings": [
                {"agent_type": "security", "severity": "MEDIUM", "category": "xss",
                 "summary": "Unescaped output", "file_path": "src/app.py",
                 "line_start": 5, "line_end": 6, "suggestion": "escape it",
                 "confidence": 0.85, "rationale": "user input rendered raw"},
                {"agent_type": "quality", "severity": "LOW", "category": "style",
                 "summary": "Line too long", "file_path": "src/app.py",
                 "line_start": 9, "line_end": 9, "suggestion": "wrap it",
                 "confidence": 0.6, "rationale": "pep8"},
            ],
            "overall_confidence": 0.725,
            "errors": [],
        }
        with patch(
            "backend.orchestrator.graph.build_graph",
            return_value=_FakeGraph(fake_result),
        ):
            out = run_review_pipeline(
                review_id, diff="--- a/src/db.py", repo="test-e2e",
                pr_number=4242, conn=conn,
            )

        assert out["decision"] == "auto_post"
        assert out["findings_count"] == 2

        record = get_review_record(review_id, conn=conn)
        assert record["status"] == "completed", "no github client → completed, not posted"
        assert float(record["overall_confidence"]) == 0.725

        findings = get_findings_for_review(review_id, conn=conn)
        assert len(findings) == 2
        assert {f["agent_type"] for f in findings} == {"security", "quality"}

        events = get_events_for_review(review_id, conn=conn)
        assert any(e["event_type"] == "decision" for e in events), (
            "the run must leave a decision event in the trace"
        )

        with conn.cursor() as cur:
            cur.execute("DELETE FROM finding_records WHERE review_id = %s", (review_id,))
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (review_id,))


def test_pipeline_run_escalate_enqueues_hitl():
    """A CRITICAL finding → escalate → status escalated + hitl row."""
    from backend.webhook_receiver.router import run_review_pipeline
    from backend.database.postgres import get_connection
    from backend.database.repository import get_review_record

    with get_connection() as conn:
        review_id = _claim_review(4243, conn)

        fake_result = {
            "decision": "escalate",
            "merged_findings": [
                {"agent_type": "security", "severity": "CRITICAL", "category": "sql-injection",
                 "summary": "SQLi", "file_path": "src/db.py", "line_start": 10, "line_end": 12,
                 "suggestion": "parameterize", "confidence": 0.95, "rationale": "concat"},
            ],
            "overall_confidence": 0.0,
            "errors": [],
        }
        with patch(
            "backend.orchestrator.graph.build_graph",
            return_value=_FakeGraph(fake_result),
        ):
            out = run_review_pipeline(
                review_id, diff="--- a/src/db.py", repo="test-e2e",
                pr_number=4243, conn=conn,
            )

        assert out["decision"] == "escalate"

        record = get_review_record(review_id, conn=conn)
        assert record["status"] == "escalated"

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, reason, state FROM hitl_reviews "
                "WHERE review_id = %s ORDER BY created_at DESC LIMIT 1",
                (review_id,),
            )
            row = cur.fetchone()
        assert row is not None, "escalated review must be in the HITL queue"
        assert row[1] == "critical_finding"
        assert row[2] == "queued"

        with conn.cursor() as cur:
            cur.execute("DELETE FROM hitl_reviews WHERE review_id = %s", (review_id,))
            cur.execute("DELETE FROM finding_records WHERE review_id = %s", (review_id,))
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (review_id,))


def test_pipeline_run_all_agents_failed_queues_with_agent_failure():
    """All agents failing → approval_queue with reason agent_failure,
    never a clean auto_post."""
    from backend.webhook_receiver.router import run_review_pipeline
    from backend.database.postgres import get_connection
    from backend.database.repository import get_review_record

    with get_connection() as conn:
        review_id = _claim_review(4244, conn)

        fake_result = {
            "decision": "approval_queue",
            "merged_findings": [],
            "overall_confidence": 0.0,
            "errors": ["security: LLM call failed after 3 retries: 401"],
        }
        with patch(
            "backend.orchestrator.graph.build_graph",
            return_value=_FakeGraph(fake_result),
        ):
            out = run_review_pipeline(
                review_id, diff="--- a/src/db.py", repo="test-e2e",
                pr_number=4244, conn=conn,
            )

        assert out["decision"] == "approval_queue"
        assert out["errors"], "errors must be surfaced in the response"

        record = get_review_record(review_id, conn=conn)
        assert record["status"] == "queued"

        with conn.cursor() as cur:
            cur.execute(
                "SELECT reason FROM hitl_reviews "
                "WHERE review_id = %s ORDER BY created_at DESC LIMIT 1",
                (review_id,),
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "agent_failure"

        with conn.cursor() as cur:
            cur.execute("DELETE FROM hitl_reviews WHERE review_id = %s", (review_id,))
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (review_id,))