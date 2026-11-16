"""Exponential backoff between attempts while InfluxDB is unavailable (1 s … 5 min)."""

import time
from collections.abc import Callable


class BackoffPolicy:
    def __init__(
        self,
        *,
        minimum_s: float = 1.0,
        maximum_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.minimum_s = minimum_s
        self.maximum_s = maximum_s
        self.clock = clock
        self.delay_s = 0.0
        self._next_attempt = 0.0

    def ready(self) -> bool:
        return self.clock() >= self._next_attempt

    def failed(self) -> float:
        """Doubles the delay (at least the minimum) and returns it."""
        self.delay_s = min(self.maximum_s, max(self.minimum_s, self.delay_s * 2))
        self._next_attempt = self.clock() + self.delay_s
        return self.delay_s

    def succeeded(self) -> None:
        self.delay_s = 0.0
        self._next_attempt = 0.0

    def allow_now(self) -> None:
        """One immediate attempt, e.g. at shutdown."""
        self._next_attempt = 0.0
