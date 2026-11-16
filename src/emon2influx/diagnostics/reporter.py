"""Façade for reporting a problem: counts it, logs it within the rate limit and, for rejected
messages, keeps the raw message in quarantine."""

from pathlib import Path

from ..metrics import Metrics
from .error_log import ErrorLog
from .events import ErrorEvent
from .quarantine import QuarantineStore
from .rate_limit import Decision, RateLimiter


class ErrorReporter:
    def __init__(
        self,
        metrics: Metrics,
        limiter: RateLimiter,
        error_log: ErrorLog,
        quarantine: QuarantineStore | None,
    ) -> None:
        self.metrics = metrics
        self.limiter = limiter
        self.error_log = error_log
        self.quarantine = quarantine

    @classmethod
    def create(
        cls, metrics: Metrics, *, limit_per_hour: int, quarantine_file: Path | None
    ) -> "ErrorReporter":
        quarantine = QuarantineStore(quarantine_file) if quarantine_file else None
        return cls(metrics, RateLimiter(limit_per_hour), ErrorLog(), quarantine)

    def report(
        self,
        event: ErrorEvent,
        *,
        written: bool = False,
        raw: bytes | str | None = None,
        quarantine: bool = False,
        count: int = 1,
    ) -> None:
        """`written`: the data was still stored (a warning); `quarantine`: keep `raw`."""
        self.metrics.error(event.category.value, count)
        decision = self.limiter.decide(event.signature, event.category.value)
        if decision is not Decision.COUNT:
            self.error_log.write(
                event,
                level=ErrorLog.level(event, written=written),
                limit_reached=decision is Decision.LAST,
            )
        if quarantine and self.quarantine:
            self.quarantine.add(event, raw)
