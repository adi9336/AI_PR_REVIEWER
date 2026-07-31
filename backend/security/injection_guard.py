"""injection_guard — detect prompt-injection in untrusted diffs (INV-3).

The diff is untrusted input from a PR. Before it's fed to any LLM, the
injection guard scans for adversarial prompt patterns that would instruct
the model to ignore its instructions, approve everything, or exfiltrate data.

If injection is detected, the guard raises InjectionDetected and the diff
is not sent to the LLM. The agent emits an escalation event instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class InjectionDetectResult:
    detected: bool
    pattern: str | None = None
    matched_text: str | None = None


# Patterns that indicate prompt injection attempts.
# These are checked case-insensitively against the raw diff.
INJECTION_PATTERNS: list[tuple[str, str]] = [
    # Direct instruction override
    (
        r"ignore\s+(all\s+)?(previous|prior|above|system)\s+instructions?",
        "ignore-previous-instructions",
    ),
    (
        r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions?",
        "ignore-previous-instructions",
    ),
    # Role/system override attempts
    (
        r"you\s+are\s+(now|actually)\s+(a|an)\s+(helpful\s+)?assistant\s+that\s+approves?",
        "role-override",
    ),
    # Approval bypass
    (
        r"(approve|approve\s+this|approve\s+everything|approve\s+all)\s+(this\s+)?(pr|pull\s+request|code|diff|change)s?",
        "approval-bypass",
    ),
    (
        r"always\s+(approve|return\s+'?approved'?)",
        "approval-bypass",
    ),
    # Output manipulation
    (
        r"(return\s+only|respond\s+with\s+only|output\s+only)\s+['\"]?(approved|no\s+findings?|clean)['\"]?",
        "output-manipulation",
    ),
    (
        r"(do\s+not|don'?t|never)\s+(report|find|flag|raise)\s+(any\s+)?(issues?|findings?|vulnerabilities?)",
        "output-manipulation",
    ),
    # System prompt exfiltration
    (
        r"(print|show|reveal|display|repeat|output)\s+(your|the|all)\s+(system\s+)?prompt",
        "prompt-exfiltration",
    ),
    # Developer/system note injection (common in well-crafted attacks)
    (
        r"<(system|developer|admin|root)\s*>",
        "tag-injection",
    ),
    (
        r"##\s*(system|developer|admin)\s*(note|message|instruction)",
        "tag-injection",
    ),
]


def check_injection(text: str) -> InjectionDetectResult:
    """Check if text contains prompt-injection patterns.

    Returns InjectionDetectResult with detected=True if any pattern matches.
    """
    if not text:
        return InjectionDetectResult(detected=False)

    for pattern, name in INJECTION_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return InjectionDetectResult(
                detected=True,
                pattern=name,
                matched_text=match.group(0)[:200],  # truncate long matches
            )

    return InjectionDetectResult(detected=False)


def is_safe(text: str) -> bool:
    """True if the text passes the injection guard (no injection detected)."""
    return not check_injection(text).detected


def sanitize_diff(diff: str) -> str:
    """Return the diff if safe, or raise if injection is detected."""
    from backend.core.exceptions import InjectionDetected

    result = check_injection(diff)
    if result.detected:
        raise InjectionDetected(
            f"Prompt injection detected in diff (pattern={result.pattern}): "
            f"{result.matched_text!r}"
        )
    return diff