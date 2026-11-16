"""All counters in one place; read and reset once per summary (doc/architecture.md §8)."""

import threading
from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricsSnapshot:
    counters: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)
    kits: frozenset[str] = frozenset()
    unknown_codes: Counter[str] = field(default_factory=Counter)


class Metrics:
    """Thread-safe: the MQTT thread, the worker and the sink thread all report here.

    Counters used: messages, rejected, points (pipeline); written, spooled, dropped, rejected_points
    (sink); queue_dropped (source). Errors are counted per category.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self._counters: Counter[str] = Counter()
        self._errors: Counter[str] = Counter()
        self._kits: set[str] = set()
        self._unknown: Counter[str] = Counter()

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def error(self, category: str, amount: int = 1) -> None:
        with self._lock:
            self._errors[category] += amount

    def kit(self, signature: str) -> None:
        with self._lock:
            self._kits.add(signature)

    def unknown_codes(self, codes: list[str]) -> None:
        if codes:
            with self._lock:
                self._unknown.update(codes)

    def snapshot(self, *, reset: bool = False) -> MetricsSnapshot:
        with self._lock:
            snap = MetricsSnapshot(
                Counter(self._counters),
                Counter(self._errors),
                frozenset(self._kits),
                Counter(self._unknown),
            )
            if reset:
                self._reset()
            return snap
