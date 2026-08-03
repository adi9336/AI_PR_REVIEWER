"""M12 gate — tooling & sandboxing.

Tests:
  1. Capability matrix: read-only specialists, tests may run the sandboxed
     runner, no one writes; unknown agents get nothing (fail closed).
  2. Registry: register + call; unknown tool rejected; out-of-scope call
     rejected (CapabilityError); duplicate registration rejected; every
     call emits one tool.call event (INV-6, monkeypatched emitter).
  3. Registry timeout: a slow tool is cut at its declared timeout (INV-4).
  4. Sandbox policy layer: scrub_env drops secret-looking vars (no Docker).
  5. Sandbox Docker layer (skipif docker unavailable): secrets never reach
     the container, --network none blocks sockets, a sleeping payload is
     killed at the timeout, known commands run.
  6. Model router: env override → step default → global default.

The registry/scope/router tests are deterministic and need no live LLM or
database; the Docker tests use python:3.11-slim (pulled on first run).
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import pytest

from backend.tools.capability_scope import (
    CapabilityError,
    allowed_tools,
    check_capability,
)
from backend.tools.model_router import DEFAULT_MODEL, resolve_model
from backend.tools.sandbox import (
    Sandbox,
    SandboxError,
    SandboxTimeoutError,
    docker_available,
    scrub_env,
)
from backend.tools.tool_registry import (
    DuplicateToolError,
    ToolError,
    ToolSpec,
    ToolTimeoutError,
    ToolRegistry,
    UnknownToolError,
)


# ── 1. Capability matrix ────────────────────────────────────────────────


def test_security_quality_read_only():
    for agent in ("security", "quality"):
        assert allowed_tools(agent) >= {"read_file", "grep", "list_dir"}
        assert "run_tests" not in allowed_tools(agent)
        with pytest.raises(CapabilityError):
            check_capability(agent, "run_tests")


def test_tests_agent_may_run_sandboxed_runner():
    assert "run_tests" in allowed_tools("tests")
    check_capability("tests", "run_tests")  # must not raise


def test_docs_least_privileged():
    assert allowed_tools("docs") == frozenset({"read_file", "grep"})
    with pytest.raises(CapabilityError):
        check_capability("docs", "list_dir")


def test_unknown_agent_fails_closed():
    assert allowed_tools("attacker") == frozenset()
    with pytest.raises(CapabilityError):
        check_capability("attacker", "read_file")


# ── 2. Registry ─────────────────────────────────────────────────────────


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolSpec(name="read_file", fn=lambda path: f"content:{path}"))
    reg.register(ToolSpec(name="grep", fn=lambda pattern: [pattern]))
    reg.register(ToolSpec(
        name="run_tests",
        fn=lambda: ["python", "-c", "print('tests ok from sandbox')"],
        sandboxed=True,
    ))
    return reg


def test_registry_call_and_names():
    reg = _registry()
    assert reg.tool_names() == ["grep", "read_file", "run_tests"]
    assert reg.call("read_file", "security", emit_event=False, path="a.py") == "content:a.py"


def test_unknown_tool_rejected():
    reg = _registry()
    with pytest.raises(UnknownToolError, match="unknown tool 'rm -rf /'"):
        reg.call("rm -rf /", "security", emit_event=False)


def test_out_of_scope_call_rejected():
    reg = _registry()
    with pytest.raises(CapabilityError, match="may not call 'run_tests'"):
        reg.call("run_tests", "security", emit_event=False)


def test_duplicate_registration_rejected():
    reg = _registry()
    with pytest.raises(DuplicateToolError, match="already registered"):
        reg.register(ToolSpec(name="read_file", fn=lambda: None))


def test_tool_call_emits_event(monkeypatch):
    captured: list[dict] = []

    def fake_emit(review_id, agent, event_type, **kwargs):
        captured.append({"agent": agent, "event_type": event_type, **kwargs})
        return uuid.uuid4()

    monkeypatch.setattr("backend.observability.events.emit_agent_event", fake_emit)

    reg = _registry()
    rid = str(uuid.uuid4())
    reg.call("read_file", "security", review_id=rid, path="a.py")

    assert len(captured) == 1
    ev = captured[0]
    assert ev["agent"] == "security"
    assert ev["event_type"] == "tool.call"
    assert ev["payload"]["tool"] == "read_file"
    assert ev["payload"]["status"] == "ok"
    assert ev["latency_ms"] is not None


def test_tool_call_error_event(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(
        "backend.observability.events.emit_agent_event",
        lambda review_id, agent, event_type, **kwargs: captured.append(kwargs) or uuid.uuid4(),
    )
    reg = ToolRegistry()
    reg.register(ToolSpec(name="read_file", fn=lambda path: 1 / 0))  # in-scope name

    with pytest.raises(ZeroDivisionError):
        reg.call("read_file", "security", review_id=str(uuid.uuid4()), path="a.py")

    assert captured[0]["payload"]["status"] == "error"
    assert "error" in captured[0]["payload"]


def test_emit_requires_review_id():
    reg = _registry()
    with pytest.raises(ValueError, match="review_id is required"):
        reg.call("read_file", "security", emit_event=True, path="a.py")


def test_denied_calls_emit_error_events(monkeypatch):
    """INV-6 records the attempt: denials emit a status=error event too."""
    captured: list[dict] = []
    monkeypatch.setattr(
        "backend.observability.events.emit_agent_event",
        lambda review_id, agent, event_type, **kwargs: captured.append(
            {"agent": agent, "event_type": event_type, **kwargs}
        ) or uuid.uuid4(),
    )
    reg = _registry()
    rid = str(uuid.uuid4())

    with pytest.raises(CapabilityError):
        reg.call("run_tests", "security", review_id=rid)
    with pytest.raises(UnknownToolError):
        reg.call("nope", "security", review_id=rid)

    assert len(captured) == 2
    assert all(c["event_type"] == "tool.call" for c in captured)
    assert all(c["payload"]["status"] == "error" for c in captured)
    assert captured[0]["payload"]["denied"] is True
    assert captured[0]["payload"]["error"] == "out of scope"
    assert captured[1]["payload"]["error"] == "unknown tool"


# ── 3. Registry timeout (INV-4) ─────────────────────────────────────────


def test_tool_timeout_cut_short():
    import time

    reg = ToolRegistry()
    # "grep" is in every read-only agent's scope — reach the timeout path.
    reg.register(ToolSpec(name="grep", fn=lambda pattern: time.sleep(5), timeout_seconds=0.2))

    start = time.monotonic()
    with pytest.raises(ToolTimeoutError, match="0.2s timeout"):
        reg.call("grep", "security", emit_event=False, pattern="x")
    assert time.monotonic() - start < 3, "timeout must cut the call short"


# ── 4. Sandbox policy layer (no Docker needed) ──────────────────────────


def test_scrub_env_drops_secrets():
    env = {
        "PATH": "/usr/bin",
        "HOME": "/root",
        "OPENAI_API_KEY": "sk-secret",
        "GITHUB_TOKEN": "ghp-secret",
        "TIGER_DATABASE_URL": "postgres://user:pass@host/db",
        "GITHUB_WEBHOOK_SECRET": "shh",
        "MODEL_REASONING": "gpt-4o-mini",
    }
    scrubbed = scrub_env(env)
    assert scrubbed["PATH"] == "/usr/bin"
    assert scrubbed["HOME"] == "/root"
    for secret in ("OPENAI_API_KEY", "GITHUB_TOKEN", "TIGER_DATABASE_URL", "GITHUB_WEBHOOK_SECRET", "MODEL_REASONING"):
        assert secret not in scrubbed, f"{secret} must never cross into the sandbox"


def test_scrub_env_keeps_safe_vars():
    env = {"PATH": "/bin", "LANG": "en_US.UTF-8", "TZ": "UTC"}
    scrubbed = scrub_env(env)
    assert scrubbed == env


# ── 5. Sandbox Docker layer (skipif docker unavailable) ─────────────────


pytestmark_docker = pytest.mark.skipif(
    not docker_available(), reason="docker not available — skipping live sandbox tests"
)


@pytest.mark.skipif(not docker_available(), reason="docker not available")
def test_sandbox_runs_known_command():
    result = Sandbox().run(["python", "-c", "print('hi from sandbox')"])
    assert result.exit_code == 0
    assert "hi from sandbox" in result.stdout


@pytest.mark.skipif(not docker_available(), reason="docker not available")
def test_sandboxed_tool_runs_inside_container():
    """Gate 5: a sandboxed tool's command executes INSIDE the container,
    not on the host."""
    reg = ToolRegistry()
    reg.register(ToolSpec(
        name="run_tests",
        fn=lambda: ["python", "-c", "print('tests ok from sandbox')"],
        sandboxed=True,
    ))
    result = reg.call("run_tests", "tests", emit_event=False)
    assert result["exit_code"] == 0
    assert "tests ok from sandbox" in result["stdout"]


def test_sandboxed_tool_fails_closed_without_docker(monkeypatch):
    """Without Docker the sandboxed gate must refuse, never run on host."""
    monkeypatch.setattr("backend.tools.sandbox.docker_available", lambda: False)
    reg = ToolRegistry()
    reg.register(ToolSpec(
        name="run_tests",
        fn=lambda: ["python", "-c", "print('evil')"],
        sandboxed=True,
    ))
    with pytest.raises(ToolError, match="refusing to run untrusted code"):
        reg.call("run_tests", "tests", emit_event=False)


def test_sandboxed_tool_must_return_command(monkeypatch):
    """A sandboxed tool whose fn does not return a command list is rejected."""
    monkeypatch.setattr("backend.tools.sandbox.docker_available", lambda: True)
    reg = ToolRegistry()
    reg.register(ToolSpec(name="run_tests", fn=lambda: {"passed": 3}, sandboxed=True))
    with pytest.raises(ToolError, match="must return a list\\[str\\] command"):
        reg.call("run_tests", "tests", emit_event=False)


@pytest.mark.skipif(not docker_available(), reason="docker not available")
def test_sandbox_masks_secrets_inside_container():
    result = Sandbox().run(
        ["python", "-c",
         "import os; print(os.environ.get('GITHUB_TOKEN', 'ABSENT')); "
         "print(os.environ.get('PATH', 'ABSENT')[:4])"],
        env={"GITHUB_TOKEN": "supersecret", "PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
    assert result.exit_code == 0
    assert "supersecret" not in result.stdout, "secret leaked into the container!"
    assert "ABSENT" in result.stdout
    assert "/usr" in result.stdout, "safe env vars should pass through"


@pytest.mark.skipif(not docker_available(), reason="docker not available")
def test_sandbox_blocks_network():
    result = Sandbox(timeout_seconds=15).run(
        ["python", "-c",
         "import socket; socket.create_connection(('1.1.1.1', 53), timeout=5); print('CONNECTED')"]
    )
    assert result.exit_code != 0, "--network none must block sockets"
    assert "CONNECTED" not in result.stdout


@pytest.mark.skipif(not docker_available(), reason="docker not available")
def test_sandbox_kills_sleeping_payload():
    with pytest.raises(SandboxTimeoutError, match="timeout"):
        Sandbox(timeout_seconds=2).run(["python", "-c", "import time; time.sleep(30)"])


def test_sandbox_fails_closed_without_docker(monkeypatch):
    monkeypatch.setattr("backend.tools.sandbox.docker_available", lambda: False)
    with pytest.raises(SandboxError, match="refusing to run untrusted code"):
        Sandbox().run(["python", "-c", "print('x')"])


# ── 6. Model router ─────────────────────────────────────────────────────


def test_router_env_override_wins():
    env = {"MODEL_REASONING": "my-reasoning-model"}
    assert resolve_model("reasoning", env=env) == "my-reasoning-model"


def test_router_step_defaults():
    env: dict[str, str] = {}
    assert resolve_model("reasoning", env=env) == DEFAULT_MODEL
    assert resolve_model("codegen", env=env) == DEFAULT_MODEL
    assert resolve_model("embedding", env=env) == "text-embedding-3-large"


def test_router_unknown_step_falls_back():
    assert resolve_model("dance", env={}) == DEFAULT_MODEL
