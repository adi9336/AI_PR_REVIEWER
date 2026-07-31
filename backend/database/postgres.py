"""postgres — connection helper for Tiger Cloud.

Single managed connection factory. All modules that need the database
call ``get_connection()`` which reads TIGER_DATABASE_URL from the
environment once.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# Load .env once at import time so TIGER_DATABASE_URL is available
# without the caller having to think about it.
_ENV_LOADED = False
if not _ENV_LOADED:
    _env_path = Path(__file__).resolve().parents[2] / "backend" / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
    _ENV_LOADED = True


def get_tiger_url() -> str:
    """Return the TIGER_DATABASE_URL, or empty string if unset."""
    return os.getenv("TIGER_DATABASE_URL", "")


def get_connection(autocommit: bool = True) -> psycopg.Connection:
    """Open a psycopg connection to Tiger Cloud.

    Raises ConnectionError if TIGER_DATABASE_URL is not set.
    """
    url = get_tiger_url()
    if not url:
        raise ConnectionError(
            "TIGER_DATABASE_URL is not set — cannot connect to Tiger Cloud"
        )
    return psycopg.connect(url, connect_timeout=30, autocommit=autocommit)


def is_available() -> bool:
    """True if TIGER_DATABASE_URL is set and a connection can be established."""
    if not get_tiger_url():
        return False
    try:
        with get_connection() as _:
            return True
    except Exception:
        return False