"""Error log, quarantine file and rate limiting (doc/architecture.md §8)."""

from .error_log import ErrorLog
from .events import ErrorEvent
from .quarantine import QuarantineStore, mask_identifiers
from .rate_limit import RateLimiter
from .reporter import ErrorReporter

__all__ = [
    "ErrorEvent",
    "ErrorLog",
    "ErrorReporter",
    "QuarantineStore",
    "RateLimiter",
    "mask_identifiers",
]
