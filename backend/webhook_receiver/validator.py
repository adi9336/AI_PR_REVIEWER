"""validator — HMAC signature verification for GitHub webhooks.

GitHub sends X-Hub-Signature-256 = "sha256=<hex>" computed over the raw
body with the webhook secret. This module verifies it before any work.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from backend.core.exceptions import PrReviewError


def verify_signature(
    body: bytes | str,
    signature: str,
    secret: str | None = None,
) -> bool:
    """Verify the GitHub webhook HMAC signature.

    Returns True if the signature matches. Raises PrReviewError if
    no secret is configured (verification is mandatory).

    The signature is "sha256=<hex>" computed over the raw body.
    """
    if isinstance(body, str):
        body = body.encode("utf-8")

    secret = secret or os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        raise PrReviewError("GITHUB_WEBHOOK_SECRET not set — cannot verify signature")

    if not signature:
        raise PrReviewError("missing X-Hub-Signature-256 header")

    if not signature.startswith("sha256="):
        raise PrReviewError(f"invalid signature format: {signature[:20]}...")

    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    received = signature.removeprefix("sha256=")

    return hmac.compare_digest(expected, received)


def validate_webhook(
    body: bytes | str,
    headers: dict[str, str],
    secret: str | None = None,
) -> bool:
    """Verify the webhook signature from headers + body.

    Returns True if valid. Raises PrReviewError if invalid or unconfigured.
    """
    headers_lower = {k.lower(): v for k, v in headers.items()}
    signature = headers_lower.get("x-hub-signature-256", "")
    return verify_signature(body, signature, secret)