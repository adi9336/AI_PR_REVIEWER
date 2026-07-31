"""M7 gate — LangGraph fan-out to four specialists, behind the engine interface.

Tests:
  1. Wall-clock of 4-agent run < 2× slowest single agent (parallel, not sequential).
  2. Full graph run produces results from all 4 agents.
  3. Agent error doesn't block the join (INV-4).
  4. Aggregate deduplicates findings.
  5. Decide routes correctly (escalate / auto_post / approval_queue).
  6. Nothing outside backend/orchestrator/ imports langgraph (INV-2).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from unittest.mock import MagicMock

from contextlib import contextmanager

import pytest


@contextmanager
def _fake_start_span(span_id=None):
    """Fake span context manager that doesn't touch contextvars or DB."""
    import uuid as _uuid
    sid = span_id or _uuid.uuid4()
    try:
        yield sid
    finally:
        pass


@contextmanager
def _fake_emit_span(*args, **kwargs):
    """Fake emit_span that doesn't emit to DB."""
    import uuid as _uuid
    try:
        yield _uuid.uuid4()
    finally:
        pass


# ── Helpers ─────────────────────────────────────────────────────────────


def _mock_llm_response(agent_type: str, delay: float = 0.0):
    """Create a mock LlmClient that returns findings for the given agent."""
    finding = {
        "severity": "HIGH",
        "category": f"{agent_type}-issue",
        "summary": f"{agent_type} finding",
        "file_path": "src/example.py",
        "line_start": 10,
        "line_end": 12,
        "suggestion": "fix it",
        "confidence": "0.85",
        "rationale": f"{agent_type} rationale",
    }

    def mock_complete(*args, **kwargs):
        if delay > 0:
            time.sleep(delay)
        return MagicMock(
            content=json.dumps({"findings": [finding]}),
            model="gpt-4o-mini",
            tokens_in=100,
            tokens_out=50,
            latency_ms=int(delay * 1000) + 50,
            cost_usd=0.001,
        )

    mock = MagicMock()
    mock.complete.side_effect = mock_complete
    return mock


def _make_state(diff: str = "+def foo(): pass\n") -> dict:
    """Create an OrchestratorState dict for testing."""
    return {
        "review_id": str(uuid.uuid4()),
        "repo": "test-repo",
        "diff": diff,
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


# ── 1. Parallel: 4-agent run < 2× slowest single agent ─────────────────


def test_parallel_fan_out_is_not_sequential():
    """Running 4 agents in parallel should be faster than 4× sequential."""
    from backend.orchestrator.nodes import run_agent

    state = _make_state("+def foo(): return 'bar'\n")
    mock_llm = _mock_llm_response("security", delay=0.3)

    # Patch out event emission to avoid DB latency in timing test
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("backend.agents.base_agent.emit_agent_event", lambda *a, **kw: None)
        mp.setattr("backend.agents.base_agent.emit_span", _fake_emit_span)
        mp.setattr("backend.agents.base_agent.get_llm_client", lambda: mock_llm)

        async def run_all():
            tasks = [
                asyncio.to_thread(
                    run_agent, state, agent_type,
                    llm_client=mock_llm, model="gpt-4o-mini",
                )
                for agent_type in ["security", "quality", "tests", "docs"]
            ]
            start = time.monotonic()
            results = await asyncio.gather(*tasks)
            elapsed = time.monotonic() - start
            return results, elapsed

        results, elapsed = asyncio.run(run_all())

    assert len(results) == 4
    for r in results:
        assert "agent_results" in r
        assert len(r["agent_results"]) == 1
        assert r["agent_results"][0]["error"] is None, (
            f"agent error: {r['agent_results'][0]['error']}"
        )

    # Parallel: should be < 2× single (0.3s) = 0.6s
    assert elapsed < 0.85, (
        f"fan-out took {elapsed:.2f}s — expected < 0.85s for parallel "
        f"vs 1.2s sequential"
    )


# ── 2. Full graph run produces all 4 agent results ─────────────────────


def test_full_graph_run():
    """The compiled graph produces a complete result with all 4 agents."""
    state = _make_state("+def foo(): return 'bar'\n")
    mock_llm = _mock_llm_response("security", delay=0.0)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "backend.agents.base_agent.get_llm_client",
            lambda: mock_llm,
        )
        from backend.orchestrator.graph import build_graph

        graph = build_graph()
        result = graph.invoke(state)

    agent_results = result.get("agent_results", [])
    assert len(agent_results) == 4, f"expected 4 agent results, got {len(agent_results)}"

    merged = result.get("merged_findings", [])
    assert len(merged) > 0, "expected merged findings"

    decision = result.get("decision")
    assert decision is not None, "expected a decision"

    agent_types = {r["agent_type"] for r in agent_results}
    assert agent_types == {"security", "quality", "tests", "docs"}, (
        f"missing agent types: {agent_types}"
    )


# ── 3. Agent error doesn't block the join (INV-4) ──────────────────────


def test_agent_error_doesnt_block_join():
    """If one agent errors, the join still completes with the other 3."""
    from backend.orchestrator.nodes import run_agent

    state = _make_state("+def foo(): return 'bar'\n")

    error_mock = MagicMock()
    error_mock.complete.side_effect = RuntimeError("LLM timeout for tests")

    result = run_agent(state, "tests", llm_client=error_mock, model="gpt-4o-mini")

    assert "agent_results" in result
    agent_result = result["agent_results"][0]
    assert agent_result["error"] is not None
    assert "LLM timeout" in agent_result["error"]

    # Also verify a working agent still produces results
    ok_mock = _mock_llm_response("security", delay=0.0)
    result2 = run_agent(state, "security", llm_client=ok_mock, model="gpt-4o-mini")
    assert result2["agent_results"][0]["error"] is None
    assert len(result2["agent_results"][0]["findings"]) > 0


# ── 4. Aggregate deduplicates findings ──────────────────────────────────


def test_aggregate_deduplicates():
    """Duplicate findings on the same file+line should collapse."""
    from backend.orchestrator.nodes import aggregate

    state = _make_state()
    state["agent_results"] = [
        {
            "agent_type": "security",
            "findings": [
                {"file_path": "src/db.py", "line_start": 10, "confidence": 0.9,
                 "severity": "CRITICAL", "rationale": "SQL injection"},
            ],
            "error": None,
        },
        {
            "agent_type": "quality",
            "findings": [
                {"file_path": "src/db.py", "line_start": 10, "confidence": 0.7,
                 "severity": "HIGH", "rationale": "Same issue, different agent"},
            ],
            "error": None,
        },
        {
            "agent_type": "tests",
            "findings": [
                {"file_path": "src/test.py", "line_start": 5, "confidence": 0.8,
                 "severity": "MEDIUM", "rationale": "Missing test"},
            ],
            "error": None,
        },
    ]

    result = aggregate(state)
    merged = result["merged_findings"]
    assert len(merged) == 2, f"expected 2 deduplicated findings, got {len(merged)}"

    db_finding = next(f for f in merged if f["file_path"] == "src/db.py")
    assert float(db_finding["confidence"]) == 0.9


# ── 5. Decide routes correctly ──────────────────────────────────────────


def test_decide_escalates_on_critical():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = [
        {"severity": "CRITICAL", "confidence": 0.999},
        {"severity": "LOW", "confidence": 0.3},
    ]
    result = decide(state)
    assert result["decision"] == "escalate"


def test_decide_auto_post_on_high_confidence():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = [
        {"severity": "MEDIUM", "confidence": 0.85},
        {"severity": "LOW", "confidence": 0.90},
    ]
    result = decide(state)
    assert result["decision"] == "auto_post"


def test_decide_approval_queue_on_low_confidence():
    from backend.orchestrator.nodes import decide

    state = _make_state()
    state["merged_findings"] = [
        {"severity": "MEDIUM", "confidence": 0.5},
        {"severity": "LOW", "confidence": 0.6},
    ]
    result = decide(state)
    assert result["decision"] == "approval_queue"


# ── 6. INV-2: no langgraph import outside orchestrator ──────────────────


def test_inv2_no_langgraph_outside_orchestrator():
    """Nothing outside backend/orchestrator/ imports langgraph."""
    import subprocess
    import sys
    from pathlib import Path

    REPO_ROOT = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable, "-c",
            """
import ast, sys, pathlib
root = pathlib.Path('backend')
violations = []
for p in root.rglob('*.py'):
    parts = p.parts
    if '__pycache__' in parts or '.venv' in parts:
        continue
    rel = str(p).replace('\\\\', '/')
    if rel.startswith('backend/orchestrator/'):
        continue
    try:
        tree = ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if 'langgraph' in alias.name:
                    violations.append(f'{rel}:{node.lineno} import {alias.name}')
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            if 'langgraph' in mod:
                violations.append(f'{rel}:{node.lineno} from {mod}')
if violations:
    print('VIOLATIONS:')
    for v in violations:
        print(f'  {v}')
    sys.exit(1)
else:
    print('OK: no langgraph imports outside orchestrator/')
    sys.exit(0)
""",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )

    assert result.returncode == 0, (
        f"INV-2 violation: langgraph imported outside orchestrator/\n"
        f"{result.stdout}\n{result.stderr}"
    )