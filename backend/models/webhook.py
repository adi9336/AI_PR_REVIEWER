"""Webhook — the inbound event that starts a review.

This module imports nothing but stdlib + pydantic. (INV-1.)
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WebhookEvent(BaseModel):
    """Parsed pull_request webhook payload (untrusted input — INV-3)."""

    delivery_uuid: str = Field(description="X-GitHub-Delivery header — idempotency key (INV-5)")
    repo: str = Field(description="owner/repo, e.g. 'acme/platform'")
    pr_number: int
    head_sha: str
    title: str = Field(description="PR title — UNTRUSTED, data not instructions (INV-3)")
    body: str = Field(default="", description="PR body — UNTRUSTED, data not instructions (INV-3)")
    diff: str = Field(default="", description="The PR diff — UNTRUSTED (INV-3)")
    action: str = Field(default="opened", description="webhook action: opened/synchronize/etc")