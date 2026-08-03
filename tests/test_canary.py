"""M13 gate — evaluation canary: REAL agents vs the golden set.

The canary closes the vacuous-gate hole: it runs the actual specialist
agents (injectable mock LLM here) on each golden diff and scores their
findings against the golden expectations. The shipped golden guards ONE
load-bearing expectation (security must catch the SQLi) — that is what
makes the gate deterministic enough for CI while still chirping on real
regressions.

Tests (deterministic — no live LLM):
  1. Known-good agent (mock returns the golden finding) → F1 1.0, passed.
  2. Regressed agent (mock returns no findings) → F1 0, not passed.
  3. Wrong-severity output → partial credit, below threshold.
  4. Only the agents named in the golden PR run (multi-agent supported).
  5. CLI exit codes: all pass → 0; any fail → 1 (run_canary monkeypatched).
"""

from __future__ import annotations

import contextlib
import json
import uuid
from unittest.mock import MagicMock

import pytest

from backend.evaluation.canary import main as canary_main
from backend.evaluation.canary import run_canary
from backend.evaluation.golden_dataset import GoldenPR

_SEC_FINDING = {
    "severity": "CRITICAL", "category": "sql-injection", "summary": "SQLi",
    "file_path": "src/db.py", "line_start": 10, "line_end": 12,
    "suggestion": "parameterize", "confidence": 0.95, "rationale": "concat",
}
_QUAL_FINDING = {
    "severity": "HIGH", "category": "error handling", "summary": "no handler",
    "file_path": "src/db.py", "line_start": 11, "line_end": 12,
    "suggestion": "try/except", "confidence": 0.9, "rationale": "unhandled",
}


class _FakeLLM:
    """Serves a queue of JSON responses (one complete() call per agent)."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, messages, **kwargs):  # noqa: ANN001 — test double
        content = self._responses.pop(0)
        return MagicMock(
            content=content, model="gpt-4o-mini",
            tokens_in=10, tokens_out=5, cost_usd=0.0001, latency_ms=50,
        )


@pytest.fixture(autouse=True)
def _no_db_emits(monkeypatch):
    """review_with_events writes spans/llm.calls via events.py — stub them
    so the tests stay deterministic (no DB, no live emitter)."""
    monkeypatch.setattr(
        "backend.agents.base_agent.emit_agent_event",
        lambda review_id, agent, event_type, **kwargs: uuid.uuid4(),
    )
    monkeypatch.setattr(
        "backend.agents.base_agent.emit_span",
        lambda *args, **kwargs: contextlib.nullcontext(),
    )


def _fixture_golden() -> GoldenPR:
    """The shipped sqli_pr fixture (agents=[security])."""
    from backend.evaluation.golden_dataset import load_golden_dataset

    return next(p for p in load_golden_dataset() if p.pr_id == "sqli_pr")


def _ok(content: str) -> str:
    return json.dumps({"findings": [json.loads(content)]})


# ── 1. Known-good → pass ────────────────────────────────────────────────


def test_canary_known_good_passes():
    results = run_canary(
        [_fixture_golden()],
        llm_client=_FakeLLM([_ok(json.dumps(_SEC_FINDING))]),
        review_id=uuid.uuid4(),
    )
    assert len(results) == 1
    assert results[0].passed
    assert results[0].f1 == pytest.approx(1.0)
    assert results[0].agents_run == ["security"]
    assert results[0].expected_count == 1


# ── 2. Regressed → fail ─────────────────────────────────────────────────


def test_canary_regressed_agent_fails():
    results = run_canary(
        [_fixture_golden()],
        llm_client=_FakeLLM(['{"findings": []}']),
        review_id=uuid.uuid4(),
    )
    assert not results[0].passed
    assert results[0].f1 == pytest.approx(0.0)
    assert results[0].actual_count == 0


def test_canary_wrong_severity_fails_below_threshold():
    wrong = dict(_SEC_FINDING, severity="LOW")
    results = run_canary(
        [_fixture_golden()],
        llm_client=_FakeLLM([_ok(json.dumps(wrong))]),
        review_id=uuid.uuid4(),
    )
    # category matches (0.5) but severity doesn't → F1 0.5 < 0.8
    assert not results[0].passed
    assert results[0].f1 == pytest.approx(0.5)


# ── 3. Multi-agent golden support ───────────────────────────────────────


def test_canary_multi_agent_runs_all_named_agents():
    pr = _fixture_golden().model_copy(update={"agents": ["security", "quality"]})
    results = run_canary(
        [pr],
        llm_client=_FakeLLM(
            [_ok(json.dumps(_SEC_FINDING)), _ok(json.dumps(_QUAL_FINDING))]
        ),
        review_id=uuid.uuid4(),
    )
    assert results[0].agents_run == ["security", "quality"]
    assert results[0].f1 == pytest.approx(1.0)


def test_canary_runs_only_named_agents():
    calls: list[str] = []

    class _RecordingLLM(_FakeLLM):
        def complete(self, messages, **kwargs):
            calls.append(messages[0]["content"][:40])
            return super().complete(messages, **kwargs)

    results = run_canary(
        [_fixture_golden()],  # agents=[security] only
        llm_client=_RecordingLLM([_ok(json.dumps(_SEC_FINDING))]),
        review_id=uuid.uuid4(),
    )
    assert results[0].agents_run == ["security"]
    assert len(calls) == 1, "only the named agent must run"


# ── 4. CLI exit codes ───────────────────────────────────────────────────


def test_canary_cli_all_pass_exits_zero(monkeypatch):
    monkeypatch.setattr(
        "backend.evaluation.canary.run_canary",
        lambda **kwargs: [MagicMock(pr_id="sqli_pr", passed=True, details=[])],
    )
    assert canary_main([]) == 0


def test_canary_cli_any_fail_exits_nonzero(monkeypatch):
    monkeypatch.setattr(
        "backend.evaluation.canary.run_canary",
        lambda **kwargs: [
            MagicMock(pr_id="sqli_pr", passed=True, details=[]),
            MagicMock(pr_id="x", passed=False, details=["f1=0.3"]),
        ],
    )
    assert canary_main([]) == 1
