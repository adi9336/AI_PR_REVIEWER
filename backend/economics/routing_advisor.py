"""routing_advisor — cost-pressure model routing (M18, Phase 16 partial).

Consumes the M16 drift signal: when cost per review drifts UP past the
threshold, the advisor suggests a cheaper model for the given step (the
step's model_router default is the base). Pure + deterministic — the
system never auto-switches models; a human (or the deploy pipeline) acts
on the suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.tools.model_router import resolve_model

# step -> cheaper fallback tier when cost pressure is high.
# The current fleet is gpt-4o-mini; the floor is the same tier, so a
# step already at the cheapest model reports "already at floor".
_CHEAPER_TIERS: dict[str, str] = {
    "reasoning": "gpt-4o-mini",
    "codegen": "gpt-4o-mini",
}


@dataclass
class RoutingSuggestion:
    """The advisor's verdict for one step."""

    step: str
    suggested_model: str
    reason: str
    pressure: str  # none | high


def suggest_model(
    step: str,
    cost_drift_pct: float,
    *,
    threshold_pct: float = 20.0,
    current_model: str | None = None,
) -> RoutingSuggestion:
    """Suggest a model for `step` under cost pressure.

    - No drift / under threshold → keep the step's default.
    - Drift past threshold → suggest the cheaper tier for the step.
    - Step already on the cheapest tier → "already at floor" (no move).
    """
    base = current_model or resolve_model(step)
    cheaper = _CHEAPER_TIERS.get(step)

    if cost_drift_pct <= threshold_pct:
        return RoutingSuggestion(
            step=step,
            suggested_model=base,
            reason=(
                f"cost drift {cost_drift_pct:+.1f}% within threshold "
                f"{threshold_pct:g}% — keep {base}"
            ),
            pressure="none",
        )

    if cheaper is None or cheaper == base:
        return RoutingSuggestion(
            step=step,
            suggested_model=base,
            reason=(
                f"cost drift {cost_drift_pct:+.1f}% past threshold but "
                f"{base} is already the cheapest tier — at floor"
            ),
            pressure="high",
        )

    return RoutingSuggestion(
        step=step,
        suggested_model=cheaper,
        reason=(
            f"cost drift {cost_drift_pct:+.1f}% past threshold {threshold_pct:g}% — "
            f"switch {base} → {cheaper}"
        ),
        pressure="high",
    )
