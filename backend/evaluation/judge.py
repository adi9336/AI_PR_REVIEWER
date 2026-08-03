"""judge — scores agent findings against the golden set (M11).

The scoring core is DETERMINISTIC (precision / recall / F1) so the
regression gate never needs a live LLM key in CI:

  - A finding matches a golden entry when (file_path, line_start) agree.
  - Category match contributes 0.5, severity match contributes 0.5.
  - Greedy assignment: each golden entry can be claimed at most once,
    by the actual finding that scores highest against it.

JudgeClient is the optional LLM-as-judge wrapper on top: it adds a
qualitative one-sentence verdict for the dashboard/audit trail. The
gate itself never depends on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.evaluation.golden_dataset import GoldenFinding, GoldenPR


@dataclass
class EvaluationScore:
    """Precision / recall / F1 of the actual findings vs the golden set."""

    precision: float
    recall: float
    f1: float
    matched: float = 0.0
    expected: int = 0
    actual: int = 0

    def passes(self, min_f1: float = 0.8) -> bool:
        """True when the F1 score clears the regression threshold."""
        return self.f1 >= min_f1


def _field(f: dict[str, Any] | GoldenFinding, name: str) -> Any:
    if isinstance(f, dict):
        return f.get(name)
    return getattr(f, name)


def _loc(f: dict[str, Any] | GoldenFinding) -> tuple[Any, Any]:
    return (_field(f, "file_path"), _field(f, "line_start"))


def _category(f: dict[str, Any] | GoldenFinding) -> str:
    return str(_field(f, "category") or "").lower()


def _severity(f: dict[str, Any] | GoldenFinding) -> str:
    sev = _field(f, "severity")
    return str(getattr(sev, "value", sev)).upper()


def score_findings(
    golden: GoldenPR,
    actual: list[dict[str, Any]],
) -> EvaluationScore:
    """Score a list of findings (as persisted dicts) against a golden PR.

    Returns an EvaluationScore; call .passes(min_f1) for the gate.
    """
    expected: list[GoldenFinding] = list(golden.expected_findings)
    used: set[int] = set()
    matched_sum = 0.0

    for a in actual:
        best_i = -1
        best_pts = 0.0
        for i, g in enumerate(expected):
            if i in used:
                continue
            if _loc(a) != _loc(g):
                continue
            pts = 0.0
            if _category(a) == _category(g):
                pts += 0.5
            if _severity(a) == _severity(g):
                pts += 0.5
            if pts > best_pts:
                best_pts = pts
                best_i = i
        if best_i >= 0 and best_pts > 0:
            used.add(best_i)
            matched_sum += best_pts

    n_actual = len(actual)
    n_expected = len(expected)
    precision = matched_sum / n_actual if n_actual else 0.0
    recall = matched_sum / n_expected if n_expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    return EvaluationScore(
        precision=precision,
        recall=recall,
        f1=f1,
        matched=matched_sum,
        expected=n_expected,
        actual=n_actual,
    )


class JudgeClient:
    """LLM-as-judge wrapper — qualitative verdict over the deterministic score.

    The gate uses score_findings() only; this client exists for the
    dashboard/audit trail and is mocked in tests.
    """

    def __init__(self, llm_client: Any = None, model: str | None = None) -> None:
        from backend.tools.llm_client import get_llm_client

        self._llm = llm_client or get_llm_client()
        self._model = model

    def verdict(self, golden: GoldenPR, score: EvaluationScore) -> str:
        """Return a one-sentence qualitative verdict on the review quality."""
        resp = self._llm.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You judge whether an AI code-review output is acceptable. "
                        "Reply in one sentence, no markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Golden PR {golden.pr_id} expects {score.expected} findings; "
                        f"the agent reported {score.actual} with F1 {score.f1:.2f}. Verdict?"
                    ),
                },
            ],
            model=self._model,
            max_tokens=60,
        )
        return resp.content.strip()
