"""security_agent — the security specialist.

Takes a diff, retrieves grounding context, calls the LLM, and returns
schema-valid Finding[] — never raw prose. The injection guard sits
on the untrusted diff before it reaches the LLM (INV-3).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.agents.base_agent import BaseAgent
from backend.agents.contracts import AgentInput, AgentOutput
from backend.core.exceptions import PrReviewError
from backend.models.enums import AgentType, Severity
from backend.models.findings import Finding


class SecurityAgent(BaseAgent):
    """The security specialist agent."""

    @property
    def agent_type(self) -> str:
        return "security"

    def _parse_findings(
        self, llm_output: dict[str, Any], agent_input: AgentInput
    ) -> list[Finding]:
        """Parse the LLM JSON output into Finding objects.

        The LLM returns: {"findings": [{severity, category, summary, ...}]}
        We validate each finding and convert to a Pydantic Finding model.
        """
        raw_findings = llm_output.get("findings", [])
        if not isinstance(raw_findings, list):
            raise PrReviewError(
                f"LLM 'findings' is not a list: {type(raw_findings)}"
            )

        findings: list[Finding] = []
        for raw in raw_findings:
            if not isinstance(raw, dict):
                continue
            try:
                finding = Finding(
                    agent_type=AgentType.SECURITY,
                    severity=Severity(raw.get("severity", "INFO")),
                    category=raw.get("category", "unknown"),
                    summary=raw.get("summary", ""),
                    file_path=raw.get("file_path"),
                    line_start=raw.get("line_start"),
                    line_end=raw.get("line_end"),
                    suggestion=raw.get("suggestion"),
                    confidence=Decimal(str(raw.get("confidence", "0.5"))),
                    rationale=raw.get("rationale", ""),
                )
                findings.append(finding)
            except (ValueError, TypeError) as exc:
                raise PrReviewError(
                    f"Malformed finding from LLM: {exc}\nRaw: {raw}"
                ) from exc

        return findings