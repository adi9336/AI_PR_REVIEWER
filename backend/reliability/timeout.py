"""timeout — async/sync timeout wrapper.

Wraps a callable with a deadline. If it exceeds the timeout, raises
TimeoutError. Used for node-level timeouts in the orchestrator (INV-4).

On Windows (no SIGALRM), falls back to calling without timeout enforcement.
"""

from __future__ import annotations

import platform
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, Iterator, TypeVar

T = TypeVar("T")

_IS_WINDOWS = platform.system() == "Windows"


@contextmanager
def deadline(seconds: float) -> Iterator[None]:
    """Context manager that raises TimeoutError after `seconds` elapsed.

    Uses SIGALRM on Unix; on Windows, falls back to no enforcement.
    """
    if _IS_WINDOWS:
        yield
        return

    import signal

    def _handler(*_args: Any) -> None:
        raise TimeoutError(f"deadline exceeded after {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, _handler)  # type: ignore[attr-defined]
    signal.setitimer(signal.ITIMER_REAL, seconds)  # type: ignore[attr-defined]
    try:
        yield
    finally:
        signal.alarm(0)  # type: ignore[attr-defined]
        signal.signal(signal.SIGALRM, old_handler)  # type: ignore[attr-defined]


def call_with_timeout(
    fn: Callable[..., T],
    *args: Any,
    timeout: float = 30.0,
    **kwargs: Any,
) -> T:
    """Call fn(*args, **kwargs) with a timeout.

    On timeout, raises TimeoutError.
    On Windows (no SIGALRM), falls back to calling without timeout.
    """
    try:
        with deadline(timeout):
            return fn(*args, **kwargs)
    except TimeoutError:
        raise TimeoutError(f"call timed out after {timeout}s")