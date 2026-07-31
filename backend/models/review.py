"""Review — a PR review record and its aggregated state.

This module imports nothing but stdlib + pydantic. (INV-1.)
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.models.enums import ReviewStatus
from backend.models.findings import Finding


class PrReviewRecord(BaseModel):
    """The top-level record for one PR review, one webhook delivery."""

    id: UUID | None = None
    repo: str
    pr_number: int
    delivery_uuid: str = Field(description="X-GitHub-Delivery — the idempotency key (INV-5)")
    head_sha: str | None = None
    overall_confidence: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=Decimal("1"),
        description="aggregated confidence across all findings",
    )
    status: ReviewStatus = ReviewStatus.PENDING
    github_review_id: int | None = None


class ReviewResult(BaseModel):
    """The full output of a completed review — record + findings."""

    record: PrReviewRecord
    findings: list[Finding] = Field(default_factory=list)