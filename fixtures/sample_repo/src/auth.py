"""Authentication utilities for the sample repo."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_session_token() -> str:
    """Generate a cryptographically secure session token."""
    return secrets.token_urlsafe(32)


def verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verify a password against a stored hash using HMAC-SHA256."""
    expected = hmac.new(
        salt.encode("utf-8"), password.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, hashed)


def hash_password(password: str, salt: str) -> str:
    """Hash a password with a salt using SHA-256."""
    return hashlib.sha256(
        salt.encode("utf-8") + password.encode("utf-8")
    ).hexdigest()