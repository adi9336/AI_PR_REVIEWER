"""parser — parse GitHub pull_request webhook payload.

Extracts repo, pr_number, head_sha, and delivery_uuid from the webhook
body + headers.
"""

from __future__ import annotations

import json
from typing import Any

from backend.core.exceptions import PrReviewError
from backend.integrations.github_models import GitHubPR, GitHubRepo, PullRequestWebhook


def parse_webhook(body: bytes | str, headers: dict[str, str]) -> PullRequestWebhook:
    """Parse a raw webhook body + headers into a PullRequestWebhook.

    Raises PrReviewError for malformed payloads.
    """
    if isinstance(body, bytes):
        body_str = body.decode("utf-8")
    else:
        body_str = body

    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError as exc:
        raise PrReviewError(f"malformed webhook JSON: {exc}") from exc

    # Normalize header keys to lowercase
    headers_lower = {k.lower(): v for k, v in headers.items()}
    delivery_uuid = headers_lower.get("x-github-delivery", "")

    if not delivery_uuid:
        raise PrReviewError("missing X-GitHub-Delivery header")

    event_type = headers_lower.get("x-github-event", "")
    if event_type != "pull_request":
        raise PrReviewError(f"unsupported event type: {event_type}")

    action = payload.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        raise PrReviewError(f"unsupported action: {action}")

    repo_data = payload.get("repository", {})
    pr_data = payload.get("pull_request", {})

    if not repo_data or not pr_data:
        raise PrReviewError("webhook missing repository or pull_request")

    repo = GitHubRepo(
        name=repo_data.get("name", ""),
        full_name=repo_data.get("full_name", ""),
    )
    pr = GitHubPR(
        number=pr_data.get("number", 0),
        title=pr_data.get("title", ""),
        body=pr_data.get("body"),
        head=pr_data.get("head", {}),
        base=pr_data.get("base", {}),
    )

    return PullRequestWebhook(
        action=action,
        delivery_uuid=delivery_uuid,
        repository=repo,
        pull_request=pr,
    )


def get_head_sha(webhook: PullRequestWebhook) -> str | None:
    """Extract the head SHA from the webhook."""
    return webhook.pull_request.head.get("sha")