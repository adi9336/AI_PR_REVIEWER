"""tool_registry — the closed catalog of tools agents may call (M12).

Every call passes five gates, in order:

  1. The tool must be REGISTERED — there is no free-form command execution.
     Denials emit a `tool.call` status=error event (INV-6 records the attempt).
  2. The agent must be in the tool's allowed scope (capability_scope).
     Out-of-scope denials also emit a status=error event.
  3. The call runs with an explicit timeout (INV-4) — enforced with a
     thread pool for pure-Python tools; sandboxed tools get a hard timeout
     inside the Docker sandbox.
  4. One `tool.call` event lands in agent_events (INV-6) with latency.
  5. Sandboxed tools (ToolSpec.sandboxed=True) execute INSIDE the Docker
     sandbox (sandbox.py) — no network, scrubbed secrets, resource limits.
     A sandboxed tool's fn returns the command list to run; the registry
     never runs it on the host. Without Docker the sandbox fails closed.

The registry never decides *what* an agent may do — capability_scope does.
The registry enforces that whatever runs is named, scoped, timed and traced.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Callable


class ToolError(Exception):
    """Base class for tool registry failures."""


class UnknownToolError(ToolError):
    """Raised when an unregistered tool name is called."""


class DuplicateToolError(ToolError):
    """Raised when a tool name is registered twice."""


class ToolTimeoutError(ToolError):
    """Raised when a tool call exceeds its declared timeout (INV-4)."""


@dataclass(frozen=True)
class ToolSpec:
    """A registered tool: a named, documented callable with a timeout.

    For sandboxed tools (sandboxed=True), `fn` receives the call kwargs and
    must return the command list (`list[str]`) to execute inside the Docker
    sandbox. The result of a sandboxed call is
    {"exit_code": int, "stdout": str, "stderr": str}.
    """

    name: str
    fn: Callable[..., Any]
    description: str = ""
    timeout_seconds: float = 30.0
    sandboxed: bool = False


class ToolRegistry:
    """The closed catalog. Register tools once; agents call through .call()."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Register a tool. Rejects duplicate names."""
        if spec.name in self._tools:
            raise DuplicateToolError(f"tool '{spec.name}' is already registered")
        self._tools[spec.name] = spec

    def tool_names(self) -> list[str]:
        """Sorted names of all registered tools."""
        return sorted(self._tools)

    def call(
        self,
        name: str,
        agent_type: str,
        *,
        review_id: str | None = None,
        conn: Any = None,
        emit_event: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Run a registered tool as `agent_type`, gated + timed + traced.

        Raises UnknownToolError, CapabilityError, ToolTimeoutError, ToolError
        (sandbox failures), or the tool's own exception (propagated with a
        `tool.call` error event).
        """
        spec = self._tools.get(name)
        if spec is None:
            self._emit_denial(agent_type, name, review_id, conn, emit_event,
                              reason="unknown tool")
            raise UnknownToolError(
                f"unknown tool '{name}' (registered: {self.tool_names()})"
            )

        from backend.tools.capability_scope import CapabilityError, check_capability

        try:
            check_capability(agent_type, name)
        except CapabilityError:
            self._emit_denial(agent_type, name, review_id, conn, emit_event,
                              reason="out of scope")
            raise

        start = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(spec.fn, **kwargs)
            result = future.result(timeout=spec.timeout_seconds)
        except FutureTimeoutError:
            self._emit(
                agent_type, name, review_id, conn, emit_event,
                status="error",
                latency_ms=int((time.monotonic() - start) * 1000),
                error=f"tool timeout after {spec.timeout_seconds}s",
            )
            raise ToolTimeoutError(
                f"tool '{name}' exceeded its {spec.timeout_seconds}s timeout (INV-4)"
            ) from None
        except Exception as exc:
            self._emit(
                agent_type, name, review_id, conn, emit_event,
                status="error",
                latency_ms=int((time.monotonic() - start) * 1000),
                error=str(exc)[:200],
            )
            raise
        finally:
            # Do NOT wait for a still-running tool: shutdown(wait=False) lets
            # the worker die with the process instead of blocking the caller
            # past the declared timeout (INV-4 is a hard cut, not a suggestion).
            pool.shutdown(wait=False, cancel_futures=True)

        if spec.sandboxed:
            result = self._run_sandboxed(
                spec, agent_type, name, result,
                review_id, conn, emit_event, start,
            )

        self._emit(
            agent_type, name, review_id, conn, emit_event,
            status="ok",
            latency_ms=int((time.monotonic() - start) * 1000),
        )
        return result

    def _run_sandboxed(
        self,
        spec: ToolSpec,
        agent_type: str,
        name: str,
        command: Any,
        review_id: str | None,
        conn: Any,
        emit_event: bool,
        start: float,
    ) -> dict[str, Any]:
        """Execute a sandboxed tool inside the Docker sandbox (gate 5)."""
        from backend.tools.sandbox import Sandbox, SandboxError

        if not isinstance(command, list) or not all(
            isinstance(c, str) for c in command
        ):
            err = (
                f"sandboxed tool '{name}' must return a list[str] command, "
                f"got {type(command).__name__}"
            )
            self._emit(
                agent_type, name, review_id, conn, emit_event,
                status="error",
                latency_ms=int((time.monotonic() - start) * 1000),
                error=err,
            )
            raise ToolError(err)

        try:
            sb = Sandbox(timeout_seconds=spec.timeout_seconds).run(command)
        except SandboxError as exc:
            err = str(exc)[:200]
            self._emit(
                agent_type, name, review_id, conn, emit_event,
                status="error",
                latency_ms=int((time.monotonic() - start) * 1000),
                error=err,
            )
            raise ToolError(f"sandboxed tool '{name}' failed: {err}") from exc

        if sb.exit_code != 0:
            err = f"exit {sb.exit_code}: {sb.stderr[:150]}"
            self._emit(
                agent_type, name, review_id, conn, emit_event,
                status="error",
                latency_ms=int((time.monotonic() - start) * 1000),
                error=err,
            )
            raise ToolError(
                f"sandboxed tool '{name}' exited {sb.exit_code}: {sb.stderr[:200]}"
            )

        return {"exit_code": sb.exit_code, "stdout": sb.stdout, "stderr": sb.stderr}

    def _emit(
        self,
        agent_type: str,
        name: str,
        review_id: str | None,
        conn: Any,
        emit_event: bool,
        *,
        status: str,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        """Emit one tool.call event (INV-6)."""
        if not emit_event:
            return
        if review_id is None:
            raise ValueError(
                "review_id is required when emit_event=True "
                "(agent_events.review_id is a UUID column)"
            )
        from backend.models.enums import EventType
        from backend.observability.events import emit_agent_event

        payload: dict[str, Any] = {"tool": name, "status": status}
        if error is not None:
            payload["error"] = error
        emit_agent_event(
            review_id,
            agent_type,
            EventType.TOOL_CALL,
            latency_ms=latency_ms,
            payload=payload,
            conn=conn,
        )

    def _emit_denial(
        self,
        agent_type: str,
        name: str,
        review_id: str | None,
        conn: Any,
        emit_event: bool,
        *,
        reason: str,
    ) -> None:
        """Record a denied attempt as a status=error event (best-effort).

        Denials happen before any execution; if there is no review context
        yet the attempt is still logged by the caller's logs instead.
        """
        if not emit_event or review_id is None:
            return
        from backend.models.enums import EventType
        from backend.observability.events import emit_agent_event

        emit_agent_event(
            review_id,
            agent_type,
            EventType.TOOL_CALL,
            latency_ms=0,
            payload={"tool": name, "status": "error", "error": reason, "denied": True},
            conn=conn,
        )
