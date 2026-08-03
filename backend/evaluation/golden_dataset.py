"""golden_dataset — typed golden dataset for evaluation (M11).

A GoldenPR is a fixture PR: a diff plus the findings a *correct* review
should produce (hand-authored). The judge (backend/evaluation/judge.py)
scores agent output against these; the regression gate blocks when the
score drops below the threshold.

Golden files live in fixtures/golden/*.json — one file per PR.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.models.enums import Severity


class GoldenFinding(BaseModel):
    """One expected finding in a golden PR."""

    severity: Severity
    category: str
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    summary: str = ""
    agent_type: str = ""  # which specialist should produce this finding ("" = any)


class GoldenPR(BaseModel):
    """A fixture PR with its hand-authored expected findings."""

    pr_id: str
    title: str = ""
    diff: str
    agents: list[str] = Field(default_factory=lambda: ["security", "quality", "tests", "docs"])
    expected_findings: list[GoldenFinding] = Field(default_factory=list)


def load_golden_dataset(directory: str | Path | None = None) -> list[GoldenPR]:
    """Load every golden PR from fixtures/golden/*.json.

    Raises ValueError if a file fails GoldenPR schema validation —
    a broken golden set must fail loudly, not silently drop entries.
    """
    base = Path(directory) if directory is not None else (
        Path(__file__).resolve().parents[2] / "fixtures" / "golden"
    )
    prs: list[GoldenPR] = []
    for path in sorted(base.glob("*.json")):
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        prs.append(GoldenPR.model_validate(data))
    return prs
