"""registry — prompt templates for specialist agents.

Prompts are the cognitive interface between the system and the LLM.
Each agent has a system prompt that defines its specialist mindset,
and a user prompt template that injects the diff + context.

The prompt registry loads and caches prompts so they can be edited
without touching agent code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_PROMPTS_DIR = Path(__file__).resolve().parent / "templates"
_cache: dict[str, str] = {}


def load_prompt(name: str) -> str:
    """Load a prompt template by name from the templates directory.

    Cached after first load. Raises FileNotFoundError if not found.
    """
    if name in _cache:
        return _cache[name]

    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found: {path}")
    content = path.read_text(encoding="utf-8")
    _cache[name] = content
    return content


def render_prompt(template: str, **kwargs: Any) -> str:
    """Render a prompt template with the given variables.

    Uses simple {var} substitution — no Jinja2 dependency.
    """
    return template.format(**kwargs)


def get_system_prompt(agent_type: str) -> str:
    """Load the system prompt for a given agent type."""
    return load_prompt(f"system_{agent_type}")


def get_user_prompt(agent_type: str) -> str:
    """Load the user prompt template for a given agent type."""
    return load_prompt(f"user_{agent_type}")