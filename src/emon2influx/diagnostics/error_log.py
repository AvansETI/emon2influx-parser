"""Formats error events for the error log (logger `emon2influx.errors`, file errors.log)."""

import logging

from ..errors import Category
from .events import ErrorEvent
from .quarantine import mask_identifiers

# Problems after which the data is still written are warnings; the rest are errors.
WARNINGS = frozenset({Category.FIELD, Category.MBUS_TIME, Category.CRC})


class ErrorLog:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("emon2influx.errors")

    @staticmethod
    def level(event: ErrorEvent, *, written: bool) -> int:
        if event.category == Category.PAYLOAD_SHAPE:
            return logging.DEBUG  # bare values such as `1`: expected noise
        if written and event.category in WARNINGS:
            return logging.WARNING
        return logging.ERROR

    def write(self, event: ErrorEvent, *, level: int, limit_reached: bool = False) -> None:
        parts = [f"category={event.category.value}", f"signature={event.signature or '-'}"]
        if event.meter or event.version:
            parts.append(f"meter={event.meter or '-'} version={event.version or '-'}")
        parts.append(event.message)
        if event.line:
            parts.append(f"line={mask_identifiers(event.line)!r}")
        self.logger.log(level, " ".join(parts))
        if limit_reached:
            self.logger.log(
                level,
                f"category={event.category.value} "
                f"signature={event.signature or '-'} limit reached; "
                "counting only until the next hour",
            )
