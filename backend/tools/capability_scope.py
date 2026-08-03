"""capability_scope — per-specialist least-privilege tool scoping (M12).

Each specialist may only call the tools its job requires (INV-1's cousin
at the agent level: least privilege). The default posture is READ-ONLY:
security / quality / docs inspect; only the tests agent may execute the
sandboxed runner. No specialist may write to the host.

Tool names must match registrations in backend/tools/tool_registry.py.
"""

from __future__ import annotations

# Tools the read-only specialists share.
READ_TOOLS = frozenset({"read_file", "grep", "list_dir"})

# The per-specialist capability matrix. Unknown agent types get NOTHING —
# fail closed, never fail open.
SCOPES: dict[str, frozenset[str]] = {
    "security": READ_TOOLS,
    "quality": READ_TOOLS,
    "docs": frozenset({"read_file", "grep"}),
    "tests": READ_TOOLS | frozenset({"run_tests"}),
}


class CapabilityError(Exception):
    """Raised when an agent calls a tool outside its scope."""


def allowed_tools(agent_type: str) -> frozenset[str]:
    """Return the tool set an agent type may call (empty = nothing)."""
    return SCOPES.get(agent_type, frozenset())


def check_capability(agent_type: str, tool_name: str) -> None:
    """Raise CapabilityError unless `agent_type` may call `tool_name`."""
    allowed = allowed_tools(agent_type)
    if tool_name not in allowed:
        raise CapabilityError(
            f"agent '{agent_type}' may not call '{tool_name}' "
            f"(allowed: {sorted(allowed)})"
        )
