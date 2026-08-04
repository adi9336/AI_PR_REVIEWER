"""M16 gate — Continuous Learning: drift detection + anchored alerting.

Tests:
  1. compute_delta pure math: up-direction past threshold → drifted,
     findings down-direction, zero baseline → None/not drifted,
     |delta| under threshold → not drifted, wrong direction → not drifted.
  2. DB-gated: synthetic two-window events — cost_per_review +30% →
     drifted; findings_per_review -25% → drifted; min-baseline floor
     respected (few baseline reviews → no drift flags).
  3. emit_alert writes an append-only agent_events row (alert/alerting)
     visible through the audit query.
  4. GET /audit/drift: 401 without key, 200 with key (DB-gated).
  5. CLI: prints the report, exit 0 (monkeypatched detect_drift).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / "backend" / ".env")

TIGER_URL = os.getenv("TIGER_DATABASE_URL", "")

from backend.observability.drift import compute_delta, detect_drift


# ── 1. Pure drift math ──────────────────────────────────────────────────


def test_delta_up_past_threshold_is_drift():
    delta, drifted = compute_delta(13.0, 10.0, 20.0, "up")
    assert delta == pytest.approx(30.0)
    assert drifted


def test_delta_up_under_threshold_not_drift():
    delta, drifted = compute_delta(11.0, 10.0, 20.0, "up")
    assert delta == pytest.approx(10.0)
    assert not drifted


def test_delta_down_past_threshold_is_drift():
    # findings per review collapsing is drift in the "down" direction
    delta, drifted = compute_delta(1.5, 2.0, 20.0, "down")
    assert delta == pytest.approx(-25.0)
    assert drifted


def test_delta_down_wrong_direction_not_drift():
    # cost going DOWN is good — never flagged
    delta, drifted = compute_delta(8.0, 10.0, 20.0, "up")
    assert delta == pytest.approx(-20.0)
    assert not drifted


def test_delta_zero_baseline_is_not_drift():
    delta, drifted = compute_delta(5.0, 0.0, 20.0, "up")
    assert delta is None
    assert not drifted


def test_delta_missing_values_not_drift():
    assert compute_delta(None, 10.0, 20.0, "up") == (None, False)
    assert compute_delta(10.0, None, 20.0, "down") == (None, False)


# ── 2. DB-gated: synthetic windows ──────────────────────────────────────


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live drift test"
)
def test_detect_drift_synthetic_windows():
    from backend.database.postgres import get_connection
    from backend.models.enums import EventType
    from backend.observability.events import emit_agent_event
    from backend.reliability.idempotency import claim_delivery

    now = datetime.now(timezone.utc)
    base_deliveries = [str(uuid.uuid4()) for _ in range(6)]
    win_deliveries = [str(uuid.uuid4()) for _ in range(6)]
    baseline_ids: list[str] = []
    window_ids: list[str] = []

    def _insert_event(conn: Any, rid: str, ts: datetime, cost: float) -> None:
        # Direct INSERT with explicit ts — INV-6 forbids UPDATE/DELETE on
        # agent_events, but INSERT with a provided timestamp is the audit
        # trail's own contract (the trigger rejects mutations, not appends).
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_events "
                "(review_id, agent, event_type, model, tokens_in, tokens_out, "
                " cost_usd, latency_ms, ts) "
                "VALUES (%s, 'security', 'llm.call', 'gpt-4o-mini', 10, 5, %s, 100, %s)",
                (rid, cost, ts),
            )

    with get_connection() as conn:
        try:
            # 6 baseline reviews 20 days ago: $1.00 cost, 1 LLM call each
            for d in base_deliveries:
                rid = claim_delivery(d, "drift-test", 8001, conn=conn)
                baseline_ids.append(str(rid))
                _insert_event(conn, str(rid), now - timedelta(days=10), 1.00)
            # 6 window reviews 2 days ago: $100 cost each — dwarfs the
            # co-existing real events so the drift signal dominates.
            for d in win_deliveries:
                rid = claim_delivery(d, "drift-test", 8002, conn=conn)
                window_ids.append(str(rid))
                _insert_event(conn, str(rid), now - timedelta(days=2), 100.00)

            # pr_review_records has no append-only trigger — backdate freely
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pr_review_records SET created_at = %s WHERE id = ANY(%s)",
                    (now - timedelta(days=10), baseline_ids),
                )
                cur.execute(
                    "UPDATE pr_review_records SET created_at = %s WHERE id = ANY(%s)",
                    (now - timedelta(days=2), window_ids),
                )

            report = detect_drift(
                window_days=7, baseline_days=7, threshold_pct=20.0,
                min_baseline_reviews=5, conn=conn,
            )
            cost = next(m for m in report.metrics if m.metric == "cost_per_review")
            # The append-only spine holds other real events (the window's
            # DISTINCT review count is shared), so assert the RELATIVE
            # signal: window cost above baseline and past the threshold.
            assert cost.window_value is not None and cost.baseline_value is not None
            assert cost.window_value > cost.baseline_value
            assert cost.delta_pct is not None and cost.delta_pct > 20
            assert cost.drifted
            assert report.any_drift
            assert report.baseline_reviews >= 6

            # min-baseline floor: tiny baseline → nothing flagged
            report_floor = detect_drift(
                window_days=7, baseline_days=7, threshold_pct=20.0,
                min_baseline_reviews=500, conn=conn,
            )
            assert not report_floor.any_drift
            assert all(m.delta_pct is None for m in report_floor.metrics)
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM pr_review_records WHERE repo = 'drift-test'"
                )


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live drift test"
)
def test_detect_drift_findings_collapse_is_drift():
    from backend.database.postgres import get_connection
    from backend.database.repository import insert_finding
    from backend.reliability.idempotency import claim_delivery

    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        try:
            base_ids = []
            for d in [str(uuid.uuid4()) for _ in range(6)]:
                rid = claim_delivery(d, "drift-findings", 8003, conn=conn)
                base_ids.append(str(rid))
                insert_finding(rid, {
                    "agent_type": "security", "severity": "HIGH",
                    "category": "x", "summary": "s", "file_path": "a.py",
                    "line_start": 1, "line_end": 1, "suggestion": "",
                    "confidence": 0.9, "rationale": "",
                }, conn=conn)  # 1 finding per baseline review
            win_ids = []
            for d in [str(uuid.uuid4()) for _ in range(6)]:
                rid = claim_delivery(d, "drift-findings", 8004, conn=conn)
                win_ids.append(str(rid))  # 0 findings in the window
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pr_review_records SET created_at = %s WHERE id = ANY(%s)",
                    (now - timedelta(days=10), base_ids),
                )
                cur.execute(
                    "UPDATE pr_review_records SET created_at = %s WHERE id = ANY(%s)",
                    (now - timedelta(days=2), win_ids),
                )

            report = detect_drift(
                window_days=7, baseline_days=7, threshold_pct=20.0,
                min_baseline_reviews=5, conn=conn,
            )
            findings = next(m for m in report.metrics if m.metric == "findings_per_review")
            # Window (0 findings across our 6) must sit below the baseline
            # (6 findings across our 6). The append-only spine shares the
            # window with real demo reviews, so assert direction + threshold,
            # not magnitude.
            assert findings.window_value is not None and findings.baseline_value is not None
            assert findings.window_value < findings.baseline_value
            assert findings.delta_pct is not None and findings.delta_pct < -20
            assert findings.drifted, "findings collapse must flag quality drift"
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM pr_review_records WHERE repo = 'drift-findings'"
                )


# ── 3. Anchored alerting ────────────────────────────────────────────────


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live alert test"
)
def test_emit_alert_writes_audit_visible_event():
    from backend.database.postgres import get_connection
    from backend.observability.alerting import emit_alert, alert_for_cost_spike
    from backend.observability.audit import query_audit
    from backend.reliability.idempotency import claim_delivery

    with get_connection() as conn:
        review_id = claim_delivery(str(uuid.uuid4()), "drift-alert", 8005, conn=conn)
        emit_alert(str(review_id), "WARNING", "cost_per_review",
                   "review cost $0.50 exceeds cap", conn=conn)
        alert_for_cost_spike(str(review_id), cost=0.50, cap=0.25, conn=conn)

        alerts = query_audit(review_id=review_id, event_type="alert", conn=conn)
        assert len(alerts) == 2
        assert all(a["agent"] == "alerting" for a in alerts)
        assert alerts[0]["payload"]["level"] == "WARNING"
        assert alerts[1]["payload"]["metric"] == "cost_per_review"
        assert "+100%" in alerts[1]["payload"]["message"]

        with conn.cursor() as cur:
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))


# ── 4. API ──────────────────────────────────────────────────────────────


def test_drift_api_requires_key():
    from fastapi.testclient import TestClient

    from backend.main import app

    r = TestClient(app).get("/audit/drift")
    assert r.status_code in (401, 503), "drift report must never be open"


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live drift test"
)
def test_drift_api_returns_report(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")
    r = TestClient(app).get("/audit/drift", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    body = r.json()
    assert "metrics" in body and "any_drift" in body
    assert {m["metric"] for m in body["metrics"]} == {
        "cost_per_review", "avg_llm_latency_ms", "llm_calls_per_review",
        "error_events", "findings_per_review",
    }


# ── 5. CLI ──────────────────────────────────────────────────────────────


def test_drift_cli_exits_zero(monkeypatch, capsys):
    from backend.observability.drift import main as drift_main

    class _FakeMetric:
        metric = "cost_per_review"
        direction = "up"
        window_value = 0.13
        baseline_value = 0.10
        delta_pct = 30.0
        drifted = True

    class _FakeReport:
        window_days = 7
        baseline_days = 7
        threshold_pct = 20.0
        min_baseline_reviews = 5
        baseline_reviews = 6
        metrics = [_FakeMetric()]
        any_drift = True

        def as_dict(self):
            return {"metrics": [{"metric": "cost_per_review", "drifted": True}]}

    monkeypatch.setattr(
        "backend.observability.drift.detect_drift", lambda **kwargs: _FakeReport()
    )
    assert drift_main([]) == 0
    out = capsys.readouterr().out
    assert "DRIFT DETECTED" in out
    assert "cost_per_review" in out

    # JSON mode
    assert drift_main(["--json"]) == 0
    assert '"drifted": true' in capsys.readouterr().out
