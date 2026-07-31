"""circuit_breaker — circuit breaker pattern for outbound calls.

States: CLOSED (normal) → OPEN (tripping, calls fail fast) → HALF_OPEN (probing)

When consecutive failures reach `failure_threshold`, the breaker opens.
After `recovery_timeout` seconds, it enters HALF_OPEN and allows one probe.
If the probe succeeds, it closes; if it fails, it reopens.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

from backend.core.exceptions import CircuitOpen

T = TypeVar("T")

_CLOSED = "closed"
_OPEN = "open"
_HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker for a single dependency."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        name: str = "default",
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._name = name
        self._state: str = _CLOSED
        self._failures: int = 0
        self._last_failure_time: float = 0.0

    @property
    def state(self) -> str:
        if self._state == _OPEN:
            if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                self._state = _HALF_OPEN
        return self._state

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Call fn through the circuit breaker.

        Raises CircuitOpen if the breaker is open.
        Raises the original exception if fn fails (and increments failures).
        """
        current = self.state
        if current == _OPEN:
            raise CircuitOpen(
                f"circuit breaker '{self._name}' is open "
                f"(failures={self._failures})"
            )

        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            self._on_failure()
            raise

        self._on_success()
        return result

    def _on_success(self) -> None:
        if self._state == _HALF_OPEN:
            self._state = _CLOSED
        self._failures = 0

    def _on_failure(self) -> None:
        self._failures += 1
        self._last_failure_time = time.monotonic()
        if self._state == _HALF_OPEN:
            self._state = _OPEN
        elif self._failures >= self._failure_threshold:
            self._state = _OPEN

    def reset(self) -> None:
        """Manually reset the breaker to closed."""
        self._state = _CLOSED
        self._failures = 0