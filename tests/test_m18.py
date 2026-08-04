"""M18 gate — The Partials: disputes, feedback, logging, routing advisor, API.

Tests:
  1. Disputes + feedback are anchored append-only events (agent=hitl)
     with the right payload shape, audit-visible (DB-gated).
  2. Logging emits parseable JSON lines; review_id lands in the JSON;
     secrets masked in messages.
  3. Routing advisor: no drift → keep default; drift past threshold →
     cheaper suggestion; at-floor step → 'already at floor'.
  4. HITL API: 401 without governance key; dispute without reason → 400;
     valid → 200 recorded (DB-gated for the 200).
"""

from __future__ import annotations

import io
import json
import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / "backend" / ".env")

TIGER_URL = os.getenv("TIGER_DATABASE_URL", "")


# ── 1. Disputes + feedback (DB-gated) ───────────────────────────────────


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live HITL test"
)
def test_dispute_and_feedback_are_audit_visible_events():
    from backend.database.postgres import get_connection
    from backend.hitl.dispute import list_disputes, record_dispute
    from backend.hitl.feedback import list_feedback, record_feedback
    from backend.reliability.idempotency import claim_delivery

    with get_connection() as conn:
        review_id = claim_delivery(str(uuid.uuid4()), "m18-hitl", 1001, conn=conn)
        finding_id = uuid.uuid4()

        record_dispute(str(review_id), finding_id, "not a real vuln",
                       reviewer="alice", conn=conn)
        record_feedback(str(review_id), finding_id, helpful=False,
                        note="false positive", conn=conn)

        disputes = list_disputes(review_id, conn=conn)
        assert len(disputes) == 1
        d = disputes[0]
        assert d["agent"] == "hitl"
        assert d["event_type"] == "dispute"
        assert d["payload"]["finding_id"] == str(finding_id)
        assert d["payload"]["reason"] == "not a real vuln"
        assert d["payload"]["reviewer"] == "alice"

        feedback = list_feedback(review_id, conn=conn)
        assert len(feedback) == 1
        f = feedback[0]
        assert f["event_type"] == "feedback"
        assert f["payload"]["helpful"] is False
        assert f["payload"]["note"] == "false positive"

        with conn.cursor() as cur:
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))


# ── 2. Logging ──────────────────────────────────────────────────────────


def test_logging_emits_parseable_json():
    from backend.observability.logging import get_logger, with_context

    stream = io.StringIO()
    logger = get_logger("test.m18")
    # point the handler at our stream (the module's handler is a StreamHandler)
    for handler in logger.handlers:
        handler.stream = stream

    with_context(logger, review_id="abc-123").info("claimed delivery")
    line = stream.getvalue().strip()
    record = json.loads(line)
    assert record["level"] == "INFO"
    assert record["logger"] == "test.m18"
    assert record["msg"] == "claimed delivery"
    assert record["review_id"] == "abc-123"


def test_logging_masks_secrets():
    from backend.observability.logging import get_logger

    stream = io.StringIO()
    logger = get_logger("test.m18.mask")
    for handler in logger.handlers:
        handler.stream = stream

    logger.info("key=sk-abcdefghijklmnopqrstuvwxyz123456 in the message")
    record = json.loads(stream.getvalue().strip())
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in record["msg"]
    assert "[REDACTED]" in record["msg"]


# ── 3. Routing advisor (pure) ───────────────────────────────────────────


def test_advisor_keeps_default_within_threshold():
    from backend.economics.routing_advisor import suggest_model

    s = suggest_model("reasoning", cost_drift_pct=5.0)
    assert s.pressure == "none"
    assert s.suggested_model == "gpt-4o-mini"  # the reasoning-step default


def test_advisor_suggests_cheaper_on_cost_drift():
    from backend.economics.routing_advisor import suggest_model

    # reasoning step with a pricier current model: drift past threshold
    s = suggest_model("reasoning", cost_drift_pct=45.0,
                      current_model="gpt-4o")
    assert s.pressure == "high"
    assert s.suggested_model == "gpt-4o-mini"
    assert "switch" in s.reason


def test_advisor_at_floor_reports_no_move():
    from backend.economics.routing_advisor import suggest_model

    s = suggest_model("codegen", cost_drift_pct=60.0)
    assert s.pressure == "high"
    assert "already at floor" in s.reason or "cheapest" in s.reason


def test_advisor_uses_model_router_default_as_base():
    from backend.economics.routing_advisor import suggest_model
    from backend.tools.model_router import resolve_model

    s = suggest_model("reasoning", cost_drift_pct=1.0)
    assert s.suggested_model == resolve_model("reasoning")


# ── 4. HITL API ─────────────────────────────────────────────────────────


def test_hitl_api_requires_key():
    from fastapi.testclient import TestClient

    from backend.main import app

    r = TestClient(app).post(
        f"/hitl/reviews/{uuid.uuid4()}/findings/{uuid.uuid4()}/dispute",
        json={"reason": "nope"},
    )
    assert r.status_code in (401, 503), "HITL actions must be key-protected"


def test_hitl_api_dispute_requires_reason(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")
    r = TestClient(app).post(
        f"/hitl/reviews/{uuid.uuid4()}/findings/{uuid.uuid4()}/dispute",
        json={"reason": ""},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 400


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live HITL test"
)
def test_hitl_api_dispute_records(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    from backend.database.postgres import get_connection
    from backend.reliability.idempotency import claim_delivery

    with get_connection() as conn:
        review_id = claim_delivery(str(uuid.uuid4()), "m18-api", 1002, conn=conn)

    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")
    client = TestClient(app)
    finding_id = uuid.uuid4()
    r = client.post(
        f"/hitl/reviews/{review_id}/findings/{finding_id}/dispute",
        json={"reason": "FP — input is parameterized", "reviewer": "bob"},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "recorded"
    assert body["event_type"] == "dispute"

    # visible via the API list endpoint
    r2 = client.get(
        f"/hitl/reviews/{review_id}/disputes", headers={"X-API-Key": "test-key"}
    )
    assert r2.status_code == 200
    assert r2.json()["count"] == 1
    assert r2.json()["disputes"][0]["payload"]["finding_id"] == str(finding_id)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))
