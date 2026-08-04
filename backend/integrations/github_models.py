"""github_models — Pydantic models for GitHub webhook payloads.

Only the fields we actually need from the PR webhook payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GitHubRepo(BaseModel):
    name: str
    full_name: str


class GitHubPR(BaseModel):
    number: int
    title: str = ""
    body: str | None = None
    # Real GitHub payloads nest OBJECTS here (head.user, head.repo, base.user,
    # base.repo are dicts with login/id/...). We only read sha/ref/label, so
    # the containers are permissive.
    head: dict[str, Any] = Field(default_factory=dict, description="sha, ref, label")
    base: dict[str, Any] = Field(default_factory=dict, description="sha, ref, label")


class PullRequestWebhook(BaseModel):
    """Minimal model of the pull_request webhook payload."""
    action: str
    delivery_uuid: str = Field(description="X-GitHub-Delivery header")
    repository: GitHubRepo
    pull_request: GitHubPR
    diff: str = Field(
        default="",
        description=(
            "Optional embedded PR diff. GitHub webhooks don't carry diffs — "
            "the worker fetches them via get_pr_diff() when this is empty. "
            "Test payloads embed the diff to exercise the full path offline."
        ),
    )