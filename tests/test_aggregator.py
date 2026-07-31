"""M8 gate — aggregator: merge, dedup, score, agreement notes.

Tests:
  1. Duplicate findings on same file+line collapse to one with agreed_by noted.
  2. Single-agent findings still get agreed_by = [] (no agreement).
  3. Multiple agents agreeing on same finding get agreement_count > 1.
  4. Highest confidence finding wins in the dedup.
"""

from __future__ import annotations

import uuid


def _make_state():
    return {
        "review_id": str(uuid.uuid4()),
        "repo": "test-repo",
        "diff": "",
        "context_chunks": [],
        "pr_number": None,
        "head_sha": None,
        "agent_results": [],
        "merged_findings": [],
        "overall_confidence": None,
        "decision": None,
        "errors": [],
        "model": None,
    }


# ── 1. Duplicates collapse with agreement noted ────────────────────────


def test_duplicate_findings_collapse_with_agreement():
    from backend.orchestrator.nodes import aggregate

    state = _make_state()
    state["agent_results"] = [
        {
            "agent_type": "security",
            "findings": [
                {"file_path": "src/db.py", "line_start": 10, "confidence": 0.9,
                 "severity": "CRITICAL", "rationale": "SQL injection",
                 "agent_type": "security"},
            ],
            "error": None,
        },
        {
            "agent_type": "quality",
            "findings": [
                {"file_path": "src/db.py", "line_start": 10, "confidence": 0.7,
                 "severity": "HIGH", "rationale": "Same issue, different agent",
                 "agent_type": "quality"},
            ],
            "error": None,
        },
    ]

    result = aggregate(state)
    merged = result["merged_findings"]

    assert len(merged) == 1, f"expected 1 merged finding, got {len(merged)}"
    assert merged[0]["agreement_count"] == 2, "expected agreement_count=2"
    assert "security" in merged[0]["agreed_by"]
    assert "quality" in merged[0]["agreed_by"]
    assert float(merged[0]["confidence"]) == 0.9, "highest confidence should win"


# ── 2. Single-agent findings get empty agreed_by ────────────────────────


def test_single_agent_finding_no_agreement():
    from backend.orchestrator.nodes import aggregate

    state = _make_state()
    state["agent_results"] = [
        {
            "agent_type": "tests",
            "findings": [
                {"file_path": "src/test.py", "line_start": 5, "confidence": 0.8,
                 "severity": "MEDIUM", "rationale": "Missing test",
                 "agent_type": "tests"},
            ],
            "error": None,
        },
    ]

    result = aggregate(state)
    merged = result["merged_findings"]

    assert len(merged) == 1
    assert merged[0].get("agreed_by") == []
    assert merged[0].get("agreement_count", 1) == 1


# ── 3. Three agents agree ───────────────────────────────────────────────


def test_three_agents_agree():
    from backend.orchestrator.nodes import aggregate

    state = _make_state()
    state["agent_results"] = [
        {
            "agent_type": "security",
            "findings": [
                {"file_path": "src/x.py", "line_start": 3, "confidence": 0.85,
                 "severity": "HIGH", "agent_type": "security"},
            ],
            "error": None,
        },
        {
            "agent_type": "quality",
            "findings": [
                {"file_path": "src/x.py", "line_start": 3, "confidence": 0.80,
                 "severity": "MEDIUM", "agent_type": "quality"},
            ],
            "error": None,
        },
        {
            "agent_type": "tests",
            "findings": [
                {"file_path": "src/x.py", "line_start": 3, "confidence": 0.90,
                 "severity": "HIGH", "agent_type": "tests"},
            ],
            "error": None,
        },
    ]

    result = aggregate(state)
    merged = result["merged_findings"]

    assert len(merged) == 1
    assert merged[0]["agreement_count"] == 3
    assert set(merged[0]["agreed_by"]) == {"security", "quality", "tests"}
    # Highest confidence (tests, 0.90) should win
    assert float(merged[0]["confidence"]) == 0.90


# ── 4. Different locations are not merged ───────────────────────────────


def test_different_locations_not_merged():
    from backend.orchestrator.nodes import aggregate

    state = _make_state()
    state["agent_results"] = [
        {
            "agent_type": "security",
            "findings": [
                {"file_path": "src/a.py", "line_start": 10, "confidence": 0.9,
                 "severity": "HIGH", "agent_type": "security"},
                {"file_path": "src/a.py", "line_start": 20, "confidence": 0.8,
                 "severity": "MEDIUM", "agent_type": "security"},
            ],
            "error": None,
        },
    ]

    result = aggregate(state)
    merged = result["merged_findings"]

    assert len(merged) == 2, "different line_start should NOT be merged"


# ── 5. Error agents don't contribute findings ────────────────────────────


def test_error_agents_excluded():
    from backend.orchestrator.nodes import aggregate

    state = _make_state()
    state["agent_results"] = [
        {
            "agent_type": "security",
            "findings": [
                {"file_path": "src/x.py", "line_start": 1, "confidence": 0.9,
                 "severity": "HIGH", "agent_type": "security"},
            ],
            "error": None,
        },
        {
            "agent_type": "tests",
            "findings": [
                {"file_path": "src/x.py", "line_start": 1, "confidence": 0.8,
                 "severity": "MEDIUM", "agent_type": "tests"},
            ],
            "error": "LLM timeout",
        },
    ]

    result = aggregate(state)
    merged = result["merged_findings"]

    # Only the non-error agent's finding should be in the result
    assert len(merged) == 1
    assert merged[0].get("agreed_by") == []