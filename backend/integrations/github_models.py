"""github_models — Pydantic models for GitHub webhook payloads.

Only the fields we actually need from the PR webhook payload.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GitHubRepo(BaseModel):
    name: str
    full_name: str


class GitHubPR(BaseModel):
    number: int
    title: str = ""
    body: str | None = None
    head: dict[str, str] = Field(default_factory=dict, description="sha, ref, label")
    base: dict[str, str] = Field(default_factory=dict, description="sha, ref, label")


class PullRequestWebhook(BaseModel):
    """Minimal model of the pull_request webhook payload."""
    action: str
    delivery_uuid: str = Field(description="X-GitHub-Delivery header")
    repository: GitHubRepo
    pull_request: GitHubPR