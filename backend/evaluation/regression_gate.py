"""regression_gate — blocks when evaluation scores drop (M11).

evaluate_and_gate() scores agent findings against a golden PR and
raises RegressionGateError when the F1 score falls below the
threshold — the CI "release block". Every gate run emits one
`evaluation.run` decision event with the score (INV-6 proof layer).

CLI (exit code = the gate result):

    python -m backend.evaluation.regression_gate [--findings actual.json] [--min-f1 0.8]

With no --findings it gates the golden set against itself (known-good),
which must PASS — the demo command for M11.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.evaluation.golden_dataset import GoldenPR, load_golden_dataset
from backend.evaluation.judge import EvaluationScore, score_findings
from backend.models.enums import EventType, Outcome
from backend.observability.events import emit_agent_event


class RegressionGateError(Exception):
    """Raised when evaluation scores fall below the regression threshold."""


@dataclass
class EvaluationReport:
    """The result of a gate run — persisted/printed by the caller."""

    pr_id: str
    score: EvaluationScore
    min_f1: float
    passed: bool
    details: list[str] = field(default_factory=list)


def evaluate_and_gate(
    golden: GoldenPR,
    actual: list[dict[str, Any]],
    *,
    min_f1: float = 0.8,
    review_id: str | None = None,
    conn: Any = None,
    emit_event: bool = True,
) -> EvaluationReport:
    """Score `actual` against `golden`; raise RegressionGateError if below threshold.

    With emit_event=True (default) one `decision` event with the score lands
    in agent_events — INV-6: if it cannot show its work, it has not done the work.
    """
    score = score_findings(golden, actual)
    passed = score.passes(min_f1)
    details = [
        f"expected={score.expected} actual={score.actual}",
        f"precision={score.precision:.2f} recall={score.recall:.2f} "
        f"f1={score.f1:.2f} (min {min_f1})",
    ]

    if emit_event:
        if review_id is None:
            raise ValueError(
                "review_id is required when emit_event=True "
                "(agent_events.review_id is a UUID column)"
            )
        emit_agent_event(
            review_id,
            "evaluation",
            EventType.DECISION,
            outcome=Outcome.APPROVED if passed else Outcome.REQUEST_CHANGES,
            confidence=score.f1,
            payload={
                "pr_id": golden.pr_id,
                "precision": score.precision,
                "recall": score.recall,
                "f1": score.f1,
                "min_f1": min_f1,
            },
            conn=conn,
        )

    if not passed:
        raise RegressionGateError(
            f"regression gate FAILED for {golden.pr_id}: " + "; ".join(details)
        )

    return EvaluationReport(
        pr_id=golden.pr_id, score=score, min_f1=min_f1, passed=True, details=details
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — exit code is the gate verdict (0 = pass)."""
    parser = argparse.ArgumentParser(
        description="Evaluation regression gate (M11): exit 0 = pass, 1 = block."
    )
    parser.add_argument("--dataset", default=None, help="dir of golden PR JSON files")
    parser.add_argument(
        "--findings",
        default=None,
        help="path to actual findings JSON (list of finding dicts); "
        "default = gate the golden set against itself (must pass)",
    )
    parser.add_argument("--min-f1", type=float, default=0.8)
    args = parser.parse_args(argv)

    prs = load_golden_dataset(args.dataset)
    if not prs:
        print("ERROR: golden dataset is empty", file=sys.stderr)
        return 2

    actual: list[dict[str, Any]] = []
    if args.findings:
        try:
            actual = json.loads(Path(args.findings).read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"ERROR: findings file not found: {args.findings}", file=sys.stderr)
            return 2
        except json.JSONDecodeError as exc:
            print(f"ERROR: findings file is not valid JSON: {exc}", file=sys.stderr)
            return 2

    failed = False
    for pr in prs:
        # With no --findings, gate each golden PR against its own expected
        # findings — the known-good self-check that must always pass.
        pr_actual = actual or [f.model_dump(mode="json") for f in pr.expected_findings]
        try:
            evaluate_and_gate(pr, pr_actual, min_f1=args.min_f1, emit_event=False)
            print(f"PASS {pr.pr_id}")
        except RegressionGateError as exc:
            failed = True
            print(f"FAIL {pr.pr_id}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
