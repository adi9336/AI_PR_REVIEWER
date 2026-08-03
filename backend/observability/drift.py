"""drift — Continuous Learning (M16, Phase 20): the system watches itself.

Compares the recent window against a baseline over the live aggregates /
agent_events and reports which metrics moved in their BAD direction past a
threshold: cost per review, avg LLM latency, LLM calls per review, error
events (all drift UP) and findings per review (drifts DOWN — real-world
quality decay, the canary's production twin).

Alerts are anchored to real review_ids (agent_events.review_id is NOT NULL
by INV-6); SYSTEM-level drift is a report (CLI / GET /audit/drift), never
a synthetic-UUID event.

CLI:
    python -m backend.observability.drift [--window-days 7] [--baseline-days 7]
                                          [--threshold-pct 20] [--min-baseline-reviews 5]
                                          [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.database.postgres import get_connection

# metric -> the direction that means "getting worse"
_METRIC_DIRECTIONS: dict[str, str] = {
    "cost_per_review": "up",
    "avg_llm_latency_ms": "up",
    "llm_calls_per_review": "up",
    "error_events": "up",
    "findings_per_review": "down",
}

_METRIC_SQL: dict[str, str] = {
    "cost_per_review": (
        "SELECT COALESCE(SUM(cost_usd), 0) / NULLIF(COUNT(DISTINCT review_id), 0) "
        "FROM agent_events WHERE ts >= %s AND ts < %s"
    ),
    "avg_llm_latency_ms": (
        "SELECT AVG(latency_ms) FROM agent_events "
        "WHERE event_type = 'llm.call' AND ts >= %s AND ts < %s"
    ),
    "llm_calls_per_review": (
        "SELECT COUNT(*)::float / NULLIF(COUNT(DISTINCT review_id), 0) "
        "FROM agent_events WHERE event_type = 'llm.call' AND ts >= %s AND ts < %s"
    ),
    "error_events": (
        "SELECT COUNT(*) FROM agent_events "
        "WHERE payload->>'status' = 'error' AND ts >= %s AND ts < %s"
    ),
    "findings_per_review": (
        "SELECT COUNT(f.id)::float / NULLIF(COUNT(DISTINCT r.id), 0) "
        "FROM pr_review_records r LEFT JOIN finding_records f ON f.review_id = r.id "
        "WHERE r.created_at >= %s AND r.created_at < %s"
    ),
}


@dataclass
class DriftMetric:
    """One metric compared across the two windows."""

    metric: str
    direction: str
    window_value: float | None
    baseline_value: float | None
    delta_pct: float | None  # None when baseline is 0 or below the sample floor
    drifted: bool


@dataclass
class DriftReport:
    """The full verdict: per-metric drift + the summary."""

    window_days: int
    baseline_days: int
    threshold_pct: float
    min_baseline_reviews: int
    baseline_reviews: int
    metrics: list[DriftMetric]
    any_drift: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "baseline_days": self.baseline_days,
            "threshold_pct": self.threshold_pct,
            "min_baseline_reviews": self.min_baseline_reviews,
            "baseline_reviews": self.baseline_reviews,
            "any_drift": self.any_drift,
            "metrics": [asdict(m) for m in self.metrics],
        }


def compute_delta(
    window_value: float | None,
    baseline_value: float | None,
    threshold_pct: float,
    direction: str,
) -> tuple[float | None, bool]:
    """Pure drift math.

    Returns (delta_pct, drifted). delta_pct is None when there is no
    meaningful baseline (zero or missing). Drift means the metric moved
    past the threshold in its BAD direction.
    """
    if window_value is None or baseline_value is None or baseline_value <= 0:
        return None, False
    delta_pct = (window_value - baseline_value) / baseline_value * 100.0
    if direction == "up":
        drifted = delta_pct > threshold_pct
    else:  # "down" — e.g. findings per review collapsing
        drifted = delta_pct < -threshold_pct
    return delta_pct, drifted


def _metric_value(name: str, start: datetime, end: datetime, conn: Any) -> float | None:
    def _run(cursor: Any) -> float | None:
        cursor.execute(_METRIC_SQL[name], (start, end))
        row = cursor.fetchone()
        return float(row[0]) if row is not None and row[0] is not None else None

    if conn is not None:
        with conn.cursor() as cur:
            return _run(cur)
    with get_connection() as c:
        with c.cursor() as cur:
            return _run(cur)


def _baseline_review_count(start: datetime, end: datetime, conn: Any) -> int:
    def _run(cursor: Any) -> int:
        cursor.execute(
            "SELECT COUNT(*) FROM pr_review_records WHERE created_at >= %s AND created_at < %s",
            (start, end),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    if conn is not None:
        with conn.cursor() as cur:
            return _run(cur)
    with get_connection() as c:
        with c.cursor() as cur:
            return _run(cur)


def detect_drift(
    *,
    window_days: int = 7,
    baseline_days: int = 7,
    threshold_pct: float = 20.0,
    min_baseline_reviews: int = 5,
    conn: Any = None,
) -> DriftReport:
    """Compare the recent window against the baseline and flag bad drift."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)
    baseline_start = window_start - timedelta(days=baseline_days)
    baseline_reviews = _baseline_review_count(baseline_start, window_start, conn)
    # A baseline with almost no reviews is noise for EVERY metric — the
    # sample floor gates the whole report, not just findings.
    valid_baseline = baseline_reviews >= min_baseline_reviews

    metrics: list[DriftMetric] = []
    for name, direction in _METRIC_DIRECTIONS.items():
        window_value = _metric_value(name, window_start, now, conn)
        baseline_value = _metric_value(name, baseline_start, window_start, conn)
        delta_pct, drifted = compute_delta(
            window_value, baseline_value, threshold_pct, direction
        )
        if not valid_baseline:
            delta_pct, drifted = None, False
        metrics.append(
            DriftMetric(
                metric=name,
                direction=direction,
                window_value=window_value,
                baseline_value=baseline_value,
                delta_pct=round(delta_pct, 2) if delta_pct is not None else None,
                drifted=drifted,
            )
        )

    return DriftReport(
        window_days=window_days,
        baseline_days=baseline_days,
        threshold_pct=threshold_pct,
        min_baseline_reviews=min_baseline_reviews,
        baseline_reviews=baseline_reviews,
        metrics=metrics,
        any_drift=any(m.drifted for m in metrics),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drift detection (M16, Phase 20).")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--baseline-days", type=int, default=7)
    parser.add_argument("--threshold-pct", type=float, default=20.0)
    parser.add_argument("--min-baseline-reviews", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    report = detect_drift(
        window_days=args.window_days,
        baseline_days=args.baseline_days,
        threshold_pct=args.threshold_pct,
        min_baseline_reviews=args.min_baseline_reviews,
    )

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, default=str))
        return 0

    print(
        f"Drift report · window {report.window_days}d vs baseline {report.baseline_days}d "
        f"(threshold {report.threshold_pct:g}%, min baseline reviews {report.min_baseline_reviews})"
    )
    print(f"baseline reviews: {report.baseline_reviews}")
    print(f"{'METRIC':<22} {'DIR':<5} {'WINDOW':>12} {'BASELINE':>12} {'Δ%':>8}  FLAG")
    for m in report.metrics:
        window_s = f"{m.window_value:.4g}" if m.window_value is not None else "—"
        base_s = f"{m.baseline_value:.4g}" if m.baseline_value is not None else "—"
        delta_s = f"{m.delta_pct:+.1f}" if m.delta_pct is not None else "—"
        flag = "DRIFT" if m.drifted else ""
        print(
            f"{m.metric:<22} {m.direction:<5} {window_s:>12} {base_s:>12} {delta_s:>8}  {flag}"
        )
    print("VERDICT:", "DRIFT DETECTED" if report.any_drift else "NO DRIFT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
