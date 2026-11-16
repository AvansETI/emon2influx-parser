"""Lets each (kit, category) through a fixed number of times per clock hour."""

import threading
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum


class Decision(Enum):
    FULL = "full"  # log in full
    LAST = "last"  # log in full and say that the limit is reached
    COUNT = "count"  # count only


class RateLimiter:
    def __init__(
        self, limit_per_hour: int, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.limit = limit_per_hour
        self.clock = clock
        self._seen: Counter[tuple[str | None, str]] = Counter()
        self._hour = self._current_hour()
        self._lock = threading.Lock()

    def _current_hour(self) -> str:
        return self.clock().strftime("%Y-%m-%dT%H")

    def decide(self, signature: str | None, category: str) -> Decision:
        with self._lock:
            hour = self._current_hour()
            if hour != self._hour:
                self._hour = hour
                self._seen.clear()
            key = (signature, category)
            self._seen[key] += 1
            seen = self._seen[key]
        if seen < self.limit:
            return Decision.FULL
        return Decision.LAST if seen == self.limit else Decision.COUNT
