"""dependencies — RBAC for the API surface (M14, Phase 15).

Fail-closed API-key gate for the governance endpoints (audit, explain).
No key configured on the server → 503; missing or wrong X-API-Key → 401
(compared in constant time); match → pass.
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request


def require_governance_key(request: Request) -> None:
    """Dependency: the request must present a valid GOVERNANCE_API_KEY."""
    expected = os.getenv("GOVERNANCE_API_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=503, detail="GOVERNANCE_API_KEY not configured"
        )
    provided = request.headers.get("X-API-Key", "")
    if not provided:
        raise HTTPException(status_code=401, detail="invalid API key")
    try:
        match = hmac.compare_digest(provided.encode("ascii"), expected.encode("ascii"))
    except UnicodeEncodeError:
        # Non-ASCII header values (Starlette decodes raw bytes as latin-1)
        # can never match — treat as invalid, never as a 500.
        match = False
    if not match:
        raise HTTPException(status_code=401, detail="invalid API key")
