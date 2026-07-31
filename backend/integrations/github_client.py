"""github_client — GitHub API client for posting PR reviews.

Wraps the GitHub REST API with circuit breaker + retry.
Posts a review with findings as review comments.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.core.exceptions import CircuitOpen, PrReviewError
from backend.reliability.circuit_breaker import CircuitBreaker
from backend.reliability.retry import retry_call

logger = logging.getLogger(__name__)


class GitHubClient:
    """GitHub API client with circuit breaker + retry."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        import os

        self._token = token or os.getenv("GITHUB_TOKEN", "")
        self._base_url = base_url
        self._breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=60.0,
            name="github-api",
        )

    def post_review(
        self,
        repo: str,
        pr_number: int,
        body: str,
        event: str = "COMMENT",
        *,
        head_sha: str | None = None,
    ) -> int:
        """Post a PR review. Returns the review ID.

        event: APPROVE | REQUEST_CHANGES | COMMENT
        """
        url = f"{self._base_url}/repos/{repo}/pulls/{pr_number}/reviews"
        headers = {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github+json",
        }
        payload: dict[str, Any] = {"body": body, "event": event}
        if head_sha and event != "APPROVE":
            payload["commit_id"] = head_sha

        def _post() -> dict[str, Any]:
            resp = httpx.post(url, headers=headers, json=payload, timeout=30.0)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        try:
            result = self._breaker.call(
                lambda: retry_call(_post, max_attempts=3, base_delay=1.0)
            )
            return int(result.get("id", 0))
        except CircuitOpen:
            raise
        except Exception as exc:
            raise PrReviewError(f"GitHub post_review failed: {exc}") from exc

    def get_pr_diff(self, repo: str, pr_number: int) -> str:
        """Fetch the PR diff (patch format)."""
        url = f"{self._base_url}/repos/{repo}/pulls/{pr_number}"
        headers = {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github.v3.diff",
        }

        def _get() -> str:
            resp = httpx.get(url, headers=headers, timeout=30.0)
            resp.raise_for_status()
            return resp.text

        try:
            return self._breaker.call(
                lambda: retry_call(_get, max_attempts=3, base_delay=1.0)
            )
        except CircuitOpen:
            raise
        except Exception as exc:
            raise PrReviewError(f"GitHub get_pr_diff failed: {exc}") from exc