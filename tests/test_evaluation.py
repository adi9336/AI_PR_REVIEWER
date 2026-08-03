"""M11 gate — evaluation: golden dataset + judge scoring + regression gate.

Tests:
  1. Golden dataset loads schema-valid (GoldenPR with Severity enums).
  2. Known-good findings score full marks (F1 = 1.0, passes 0.8).
  3. A missed CRITICAL finding drops recall → below threshold.
  4. Wrong severity gets partial credit, still below threshold.
  5. Gate blocks degraded output (RegressionGateError) and passes known-good.
  6. Empty output scores zero and blocks.
  7. CLI exit codes: 0 on self-gate, 1 on degraded findings.
  8. Gate run emits one `decision` event with the score (INV-6) —
     requires TIGER_DATABASE_URL, skips cleanly if unset.

The scoring core is deterministic — no live LLM key needed.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / "backend" / ".env")

TIGER_URL = os.getenv("TIGER_DATABASE_URL", "")

from backend.evaluation.golden_dataset import GoldenPR, load_golden_dataset
from backend.evaluation.judge import EvaluationScore, score_findings
from backend.evaluation.regression_gate import (
    RegressionGateError,
    evaluate_and_gate,
    main as gate_main,
)
from backend.models.enums import Severity


def _golden() -> GoldenPR:
    prs = load_golden_dataset()
    assert prs, "golden dataset must not be empty"
    return next(p for p in prs if p.pr_id == "sqli_pr")


def _expected_dicts(golden: GoldenPR) -> list[dict]:
    return [f.model_dump(mode="json") for f in golden.expected_findings]


# ── 1. Dataset schema ───────────────────────────────────────────────────


def test_golden_dataset_loads_schema_valid():
    prs = load_golden_dataset()
    assert len(prs) >= 1
    for pr in prs:
        assert pr.pr_id
        assert pr.diff
        assert isinstance(pr.expected_findings, list)
        for f in pr.expected_findings:
            assert isinstance(f.severity, Severity)
            assert f.category
            assert f.file_path


# ── 2. Known-good scoring ───────────────────────────────────────────────


def test_known_good_scores_full_marks():
    golden = _golden()
    score = score_findings(golden, _expected_dicts(golden))
    assert score.f1 == pytest.approx(1.0)
    assert score.precision == pytest.approx(1.0)
    assert score.recall == pytest.approx(1.0)
    assert score.passes(0.8)


# ── 3. Missed finding → recall drops ────────────────────────────────────


def test_missed_critical_finding_drops_score():
    golden = _golden()
    actual = [f for f in _expected_dicts(golden) if f["severity"] != "CRITICAL"]
    score = score_findings(golden, actual)
    assert score.recall == pytest.approx(0.5)
    assert score.f1 < 0.8, "missing half the golden set must fail the gate"
    assert not score.passes(0.8)


# ── 4. Wrong severity → partial credit ──────────────────────────────────


def test_wrong_severity_partial_credit():
    golden = _golden()
    actual = [
        {**f, "severity": "LOW"} if f["severity"] == "CRITICAL" else f
        for f in _expected_dicts(golden)
    ]
    score = score_findings(golden, actual)
    # category match (0.5) + correct severity on the second finding (1.0)
    assert score.matched == pytest.approx(1.5)
    assert score.f1 < 0.8
    assert not score.passes(0.8)


# ── 5. Gate behavior ────────────────────────────────────────────────────


def test_gate_blocks_degraded_output():
    golden = _golden()
    actual = [f for f in _expected_dicts(golden) if f["severity"] != "CRITICAL"]
    with pytest.raises(RegressionGateError, match="f1=0.67"):
        evaluate_and_gate(golden, actual, emit_event=False)


def test_gate_passes_known_good():
    golden = _golden()
    report = evaluate_and_gate(golden, _expected_dicts(golden), emit_event=False)
    assert report.passed
    assert report.score.f1 == pytest.approx(1.0)


def test_gate_requires_review_id_for_event():
    golden = _golden()
    with pytest.raises(ValueError, match="review_id is required"):
        evaluate_and_gate(golden, _expected_dicts(golden), emit_event=True)


# ── 6. Empty output ─────────────────────────────────────────────────────


def test_empty_actual_scores_zero_and_blocks():
    golden = _golden()
    score = score_findings(golden, [])
    assert score.precision == 0.0
    assert score.recall == 0.0
    assert score.f1 == 0.0
    with pytest.raises(RegressionGateError):
        evaluate_and_gate(golden, [], emit_event=False)


# ── 7. CLI exit codes ───────────────────────────────────────────────────


def test_cli_self_gate_exits_zero(tmp_path: Path):
    assert gate_main([]) == 0


def test_cli_degraded_findings_exits_nonzero(tmp_path: Path):
    golden = _golden()
    degraded = [f for f in _expected_dicts(golden) if f["severity"] != "CRITICAL"]
    findings_file = tmp_path / "degraded.json"
    findings_file.write_text(json.dumps(degraded), encoding="utf-8")
    assert gate_main(["--findings", str(findings_file)]) == 1


# ── 8. INV-6: one evaluation event per gate run (needs Tiger Cloud) ─────


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live evaluation event test"
)
def test_gate_run_emits_evaluation_event():
    from backend.database.postgres import get_connection
    from backend.observability.events import get_events_for_review

    golden = _golden()
    review_id = str(uuid.uuid4())

    with get_connection() as conn:
        evaluate_and_gate(golden, _expected_dicts(golden), review_id=review_id, conn=conn)
        events = get_events_for_review(review_id, conn=conn)

    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "decision"
    assert event["agent"] == "evaluation"
    assert event["outcome"] == "approved"
    assert event["confidence"] == pytest.approx(1.0)
    assert event["payload"]["f1"] == pytest.approx(1.0)
    assert event["payload"]["pr_id"] == "sqli_pr"
