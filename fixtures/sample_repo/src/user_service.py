"""Sample fixture repo for M5 retrieval tests.

This file contains a helper that was renamed from 'fetchUserData' to 'load_user_profile'.
The semantic meaning is the same, so vector (embedding) search should find it
even when the query uses different wording like 'get user data'.
"""

from __future__ import annotations

from typing import Any


def load_user_profile(user_id: int) -> dict[str, Any]:
    """Load a user profile from the database by ID.

    Args:
        user_id: The unique identifier for the user.

    Returns:
        A dictionary with the user's profile data.
    """
    # In production this would query the database
    return {"id": user_id, "name": "", "email": ""}