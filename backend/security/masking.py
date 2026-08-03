"""masking — redacts secrets before content is served to humans (M14).

The audit spine stores payloads verbatim (INV-6: if it cannot show its
work, it has not done the work). Masking happens at the READ boundary:
what an auditor or dashboard sees never leaks keys or DSNs.
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # OpenAI-style API keys
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    # GitHub tokens: ghp_ (PAT), gho_ (OAuth), ghu_ (user), ghs_ (server), ghr_ (refresh)
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    # Postgres / connection DSNs with embedded credentials (scheme kept)
    re.compile(r"(postgres(?:ql)?://)[^@\s]+@"),
    # k=v / k: v secret pairs
    re.compile(r"(?i)(secret|token|apikey|api_key|password|passwd)\s*[=:]\s*\S+"),
]


def mask_secrets(text: str) -> str:
    """Replace secret-looking substrings in `text` with [REDACTED]."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]" if pattern.groups else "[REDACTED]", text)
    return text


def mask_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy of `payload` with every string value secret-masked."""
    if payload is None:
        return None
    masked: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            masked[key] = mask_secrets(value)
        elif isinstance(value, dict):
            masked[key] = mask_payload(value)
        elif isinstance(value, list):
            masked[key] = [
                mask_payload(v) if isinstance(v, dict)
                else mask_secrets(v) if isinstance(v, str)
                else v
                for v in value
            ]
        else:
            masked[key] = value
    return masked
