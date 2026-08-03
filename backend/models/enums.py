"""Enums shared across the AI PR review agent.

This module imports nothing but stdlib + pydantic. (INV-1.)
"""

from __future__ import annotations

from enum import Enum


class AgentType(str, Enum):
    """The four specialist agent minds."""

    SECURITY = "security"
    QUALITY = "quality"
    TESTS = "tests"
    DOCS = "docs"


class Severity(str, Enum):
    """Finding severity, ordered from most to least urgent."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class ReviewStatus(str, Enum):
    """Lifecycle state of a PR review record."""

    PENDING = "pending"
    POSTED = "posted"
    QUEUED = "queued"
    ESCALATED = "escalated"
    FAILED = "failed"
    COMPLETED = "completed"


class HitlState(str, Enum):
    """State of a human-in-the-loop review queue entry."""

    QUEUED = "queued"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class EventType(str, Enum):
    """agent_events.event_type — the action verbs of the audit trail."""

    SPAN_START = "span.start"
    SPAN_END = "span.end"
    LLM_CALL = "llm.call"
    TOOL_CALL = "tool.call"
    DECISION = "decision"
    ESCALATION = "escalation"


class Outcome(str, Enum):
    """agent_events.outcome — the resolution of an action."""

    APPROVED = "approved"
    REQUEST_CHANGES = "request_changes"
    CRITICAL_BLOCK = "critical_block"
    ESCALATED = "escalated"


class HitlVerdict(str, Enum):
    """hitl_feedback.verdict — a human reviewer's judgement."""

    AGREED = "agreed"
    DISPUTED = "disputed"
    FALSE_POSITIVE = "false_positive"