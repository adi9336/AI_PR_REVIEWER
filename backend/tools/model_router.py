"""model_router — picks the model per step (M12, ADR-0002 compatible).

Resolution order for a step:
  1. MODEL_<STEP> env override (e.g. MODEL_REASONING for "reasoning")
  2. the step's default in STEP_DEFAULTS
  3. the global DEFAULT_MODEL

Defaults target OpenAI-served models because the backend LlmClient talks
to OpenAI directly (see implementation-notes deviation log). The Hermes
loop models (kimi-k3 / hy3, opencode-go provider) are NOT valid here.
"""

from __future__ import annotations

import os
from typing import Mapping

DEFAULT_MODEL = "gpt-4o-mini"

# Step -> default model (OpenAI-served).
STEP_DEFAULTS: dict[str, str] = {
    "reasoning": DEFAULT_MODEL,
    "codegen": DEFAULT_MODEL,
    "judge": DEFAULT_MODEL,
    "embedding": "text-embedding-3-large",
}

# Step -> env var that may override the default.
STEP_ENV_VARS: dict[str, str] = {
    "reasoning": "MODEL_REASONING",
    "codegen": "MODEL_CODEGEN",
    "judge": "MODEL_JUDGE",
}


def resolve_model(step: str, *, env: Mapping[str, str] | None = None) -> str:
    """Resolve the model for a step: env override → step default → global default.

    Unknown steps fall back to DEFAULT_MODEL (fail closed, never fail open).
    """
    source = dict(os.environ) if env is None else dict(env)
    env_var = STEP_ENV_VARS.get(step)
    if env_var and source.get(env_var):
        return source[env_var]
    return STEP_DEFAULTS.get(step, DEFAULT_MODEL)
