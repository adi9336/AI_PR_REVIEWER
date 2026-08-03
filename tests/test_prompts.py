"""M13 gate — CI/CD for AI: prompt versioning + ci_check gate runner.

Tests:
  1. prompt_version is deterministic per agent and 8 hex chars.
  2. prompt_version changes when a template file changes (tmp templates dir).
  3. prompt_version differs between agents (different template bytes).
  4. llm.call events carry payload.prompt_version (INV-6 traceability —
     a disputed finding must trace to the exact prompt that ran).
  5. ci_check aggregates gates: all pass → exit 0; any fail → exit 1.

The ci_check tests load scripts/ci_check.py via importlib (scripts/ is not
a package); run_gate is monkeypatched so the aggregation logic is tested
without re-running the full suite.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.agents.contracts import AgentInput
from backend.agents.security_agent import SecurityAgent
from backend.prompts import registry as prompt_registry
from backend.prompts.registry import prompt_version

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── 1-3. prompt_version semantics ───────────────────────────────────────


def test_prompt_version_is_deterministic():
    v1 = prompt_version("security")
    v2 = prompt_version("security")
    assert v1 == v2
    assert len(v1) == 8
    assert all(c in "0123456789abcdef" for c in v1)


def test_prompt_version_changes_with_template_content(tmp_path: Path, monkeypatch):
    # Build a fake templates dir with security templates, point the registry at it.
    (tmp_path / "system_security.md").write_text("system v1", encoding="utf-8")
    (tmp_path / "user_security.md").write_text("user v1", encoding="utf-8")
    monkeypatch.setattr(prompt_registry, "_PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(prompt_registry, "_cache", {})

    v1 = prompt_version("security")

    # Change the system template — the version must change.
    (tmp_path / "system_security.md").write_text("system v2", encoding="utf-8")
    monkeypatch.setattr(prompt_registry, "_cache", {})
    v2 = prompt_version("security")

    assert v1 != v2, "editing a template must bump the prompt version"


def test_prompt_versions_differ_per_agent():
    assert prompt_version("security") != prompt_version("quality")
    assert prompt_version("security") != prompt_version("tests")
    assert prompt_version("security") != prompt_version("docs")


# ── 4. llm.call events carry the prompt version (INV-6) ─────────────────


def test_llm_call_event_carries_prompt_version(monkeypatch):
    import contextlib

    captured: dict = {}

    def fake_emit(review_id, agent, event_type, **kwargs):
        captured["event_type"] = event_type
        captured.update(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr("backend.agents.base_agent.emit_agent_event", fake_emit)
    # emit_span writes real DB rows via events.py's own emitter — stub it out
    # with a callable that returns a no-op context manager.
    monkeypatch.setattr(
        "backend.agents.base_agent.emit_span",
        lambda *args, **kwargs: contextlib.nullcontext(),
    )

    mock_llm = MagicMock()
    mock_llm.complete.return_value = MagicMock(
        content='{"findings": []}',
        model="gpt-4o-mini",
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.0001,
        latency_ms=50,
    )
    agent = SecurityAgent(llm_client=mock_llm, model="gpt-4o-mini")
    ai = AgentInput(
        review_id=uuid.uuid4(), repo="test-repo", diff="+x", context_chunks=[], pr_number=1
    )
    agent.review_with_events(ai)

    et = captured["event_type"]
    et_value = et.value if hasattr(et, "value") else str(et)
    assert et_value == "llm.call"
    assert captured["payload"]["prompt_version"] == prompt_version("security"), (
        "the audit trail must record which prompt version produced the call"
    )


# ── 5. ci_check gate aggregation ────────────────────────────────────────


def _load_ci_check():
    spec = importlib.util.spec_from_file_location(
        "ci_check", REPO_ROOT / "scripts" / "ci_check.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ci_check_all_gates_pass_exits_zero(monkeypatch):
    ci = _load_ci_check()
    calls: list[str] = []
    monkeypatch.setattr(
        ci, "run_gate", lambda name, cmd: calls.append(name) or True
    )
    assert ci.main([]) == 0
    assert len(calls) == 4, "all four gates must run"
    assert "pytest" in calls and "mypy (strict)" in calls
    assert "check_deps (INV-1/2)" in calls
    assert "eval gate sanity (M11, self-check)" in calls


def test_ci_check_any_gate_fail_exits_nonzero(monkeypatch):
    ci = _load_ci_check()
    monkeypatch.setattr(ci, "run_gate", lambda name, cmd: name != "mypy (strict)")
    assert ci.main([]) == 1


def test_ci_check_runs_every_gate_even_when_one_fails(monkeypatch):
    ci = _load_ci_check()
    calls: list[str] = []
    monkeypatch.setattr(
        ci, "run_gate", lambda name, cmd: (calls.append(name) or name != "pytest")
    )
    ci.main([])
    assert len(calls) == 4, "a failing gate must not short-circuit the rest"
