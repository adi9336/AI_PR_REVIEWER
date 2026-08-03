"""canary — the REAL eval gate: agents vs the golden set (M13, Phase 18).

The regression gate in scripts/ci_check.py is a no-secret SANITY check
(golden-vs-itself). THIS is the canary that keeps the promise: for each
golden PR it runs the actual specialist agents (real LLM) on the golden
diff, scores their findings against the golden expectations, and fails
the run (exit 1) when any PR drops below min_f1.

A prompt change that makes an agent stop finding the golden issues now
fails the canary — that is the canary chirping. It needs an LLM key, so
CI runs it only when OPENAI_API_KEY is present (secrets-enabled job).

CLI:
    python -m backend.evaluation.canary [--min-f1 0.8] [--dataset DIR]

Tests inject a mock llm_client; the run is deterministic.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from backend.agents.contracts import AgentInput
from backend.agents.docs_agent import DocsAgent
from backend.agents.quality_agent import QualityAgent
from backend.agents.security_agent import SecurityAgent
from backend.agents.test_agent import TestAgent
from backend.evaluation.golden_dataset import GoldenPR, load_golden_dataset
from backend.evaluation.judge import score_findings

_AGENT_CLASSES: dict[str, type] = {
    "security": SecurityAgent,
    "quality": QualityAgent,
    "tests": TestAgent,
    "docs": DocsAgent,
}


@dataclass
class CanaryResult:
    """The canary verdict for one golden PR."""

    pr_id: str
    agents_run: list[str]
    expected_count: int
    actual_count: int
    precision: float
    recall: float
    f1: float
    passed: bool
    details: list[str] = field(default_factory=list)


def _expected_for_agents(pr: GoldenPR) -> GoldenPR:
    """Golden PR filtered to the findings attributable to pr.agents.

    Expectations with agent_type="" count for any agent (never dropped).
    """
    wanted = [f for f in pr.expected_findings if not f.agent_type or f.agent_type in pr.agents]
    return pr.model_copy(update={"expected_findings": wanted})


def run_canary(
    dataset: list[GoldenPR] | None = None,
    *,
    min_f1: float = 0.8,
    llm_client: Any = None,
    review_id: UUID | str | None = None,
    conn: Any = None,
) -> list[CanaryResult]:
    """Run the real agents on each golden diff and gate on the score.

    `llm_client` is injectable for deterministic tests; defaults to the
    real LlmClient (env key). review_id/conn flow into the agent events
    (INV-6 audit trail of the canary run itself).
    """
    prs = dataset if dataset is not None else load_golden_dataset()
    if not prs:
        raise ValueError("golden dataset is empty — nothing to canary")

    base_rid: UUID = review_id if isinstance(review_id, UUID) else uuid.UUID(str(review_id)) if review_id else uuid.uuid4()

    results: list[CanaryResult] = []
    for pr in prs:
        actual_payloads: list[dict[str, Any]] = []
        agents_run: list[str] = []
        for agent_name in pr.agents:
            agent_cls = _AGENT_CLASSES.get(agent_name)
            if agent_cls is None:
                raise ValueError(f"golden PR '{pr.pr_id}' references unknown agent '{agent_name}'")
            agent = agent_cls(llm_client=llm_client, conn=conn)
            ai = AgentInput(
                review_id=base_rid,
                repo=pr.pr_id,
                diff=pr.diff,
                context_chunks=[],
                pr_number=0,
            )
            output = agent.review_with_events(ai)
            actual_payloads.extend(f.model_dump(mode="json") for f in output.findings)
            agents_run.append(agent_name)

        golden = _expected_for_agents(pr)
        score = score_findings(golden, actual_payloads)
        passed = score.passes(min_f1)
        results.append(
            CanaryResult(
                pr_id=pr.pr_id,
                agents_run=agents_run,
                expected_count=score.expected,
                actual_count=score.actual,
                precision=score.precision,
                recall=score.recall,
                f1=score.f1,
                passed=passed,
                details=[
                    f"agents={agents_run}",
                    f"precision={score.precision:.2f} recall={score.recall:.2f} "
                    f"f1={score.f1:.2f} (min {min_f1})",
                ],
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    """CLI: run the canary with the real LLM; exit 0 iff every PR passes."""
    parser = argparse.ArgumentParser(description="Evaluation canary (M13): agents vs golden set.")
    parser.add_argument("--min-f1", type=float, default=0.8)
    parser.add_argument("--dataset", default=None, help="dir of golden PR JSON files")
    args = parser.parse_args(argv)

    try:
        results = run_canary(min_f1=args.min_f1, dataset=load_golden_dataset(args.dataset) if args.dataset else None)
    except Exception as exc:
        print(f"ERROR: canary failed to run: {exc}", file=sys.stderr)
        return 2

    failed = False
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'} {r.pr_id}: " + "; ".join(r.details))
        if not r.passed:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
