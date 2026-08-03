"""M15 gate — Frontend Dashboard API surface.

The dashboard is server-side (Next.js RSC); these tests pin the API it
consumes: GET /reviews (list) and GET /audit/reviews/{id}/trace.

Tests:
  1. list_reviews returns newest-first with the dashboard's columns
     (DB-gated).
  2. GET /reviews → 200 with reviews + count (DB-gated).
  3. GET /audit/reviews/{id}/trace → 401 without the governance key,
     400 for a bad UUID, 200 with a valid key (DB-gated for the 200).
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


# ── 1. Repository: list_reviews ─────────────────────────────────────────


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live DB test"
)
def test_list_reviews_newest_first():
    from backend.database.postgres import get_connection
    from backend.database.repository import list_reviews

    with get_connection() as conn:
        try:
            with conn.cursor() as cur:
                # two probe reviews with forced creation order
                cur.execute(
                    "INSERT INTO pr_review_records (id, repo, pr_number, status, delivery_uuid) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), "dash-test-old", 1, "pending", str(uuid.uuid4())),
                )
                cur.execute(
                    "INSERT INTO pr_review_records (id, repo, pr_number, status, delivery_uuid) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), "dash-test-new", 2, "completed", str(uuid.uuid4())),
                )
                conn.commit()
            rows = list_reviews(conn=conn)
            # newest-first: our two probe rows are the most recent
            assert rows[0]["repo"] == "dash-test-new"
            assert rows[1]["repo"] == "dash-test-old"
            assert {"id", "repo", "pr_number", "status", "overall_confidence", "created_at"} <= set(rows[0])
            # limit clamps: floor at 1, ceiling at 200
            assert len(list_reviews(limit=1, conn=conn)) == 1
            assert len(list_reviews(limit=9999, conn=conn)) <= 200
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM pr_review_records WHERE repo LIKE 'dash-test-%%'"
                )


# ── 2. GET /reviews ─────────────────────────────────────────────────────


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live DB test"
)
def test_get_reviews_endpoint_shape():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    r = client.get("/reviews")
    assert r.status_code == 200
    body = r.json()
    assert "reviews" in body and "count" in body
    assert body["count"] == len(body["reviews"])
    for item in body["reviews"]:
        assert {"id", "repo", "pr_number", "status", "overall_confidence", "created_at"} <= set(item)


# ── 3. Trace endpoint auth ───────────────────────────────────────────────


def test_trace_requires_governance_key():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    r = client.get(f"/audit/reviews/{uuid.uuid4()}/trace")
    assert r.status_code in (401, 503), "trace must never be open"


def test_trace_bad_uuid_returns_400(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")
    r = client.get(
        "/audit/reviews/not-a-uuid/trace", headers={"X-API-Key": "test-key"}
    )
    assert r.status_code == 400


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live DB test"
)
def test_trace_returns_masked_events(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")

    from backend.database.postgres import get_connection
    from backend.models.enums import EventType
    from backend.observability.events import emit_agent_event
    from backend.reliability.idempotency import claim_delivery

    delivery = str(uuid.uuid4())
    with get_connection() as conn:
        review_id = claim_delivery(delivery, "dash-trace", 7001, conn=conn)
        emit_agent_event(
            str(review_id), "security", EventType.LLM_CALL,
            model="gpt-4o-mini", tokens_in=10, tokens_out=5,
            cost_usd=0.0001, latency_ms=50,
            payload={"prompt_version": "abc12345", "leak": "sk-abcdefghijklmnopqrstuvwxyz123456"},
            conn=conn,
        )
        emit_agent_event(str(review_id), "aggregator", EventType.DECISION,
                         outcome="escalated", confidence=0.95, conn=conn)

    r = client.get(
        f"/audit/reviews/{review_id}/trace", headers={"X-API-Key": "test-key"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert [e["event_type"] for e in body["events"]] == ["llm.call", "decision"]
    # masked at the read boundary
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in str(body["events"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))
