"""Error log, quarantine file, rate limiting and hourly summary (doc/architecture.md §8)."""

import json
import logging
import re
import threading
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .errors import Category

log = logging.getLogger("emon2influx")
error_log = logging.getLogger("emon2influx.errors")

# Identifier values that never leave the process, not even in the quarantine file (§4.4).
_IDENTIFIERS = re.compile(r"(0-\d+:96\.1\.[01]\(|0-0:96\.13\.[01]\()[^)]*(\))")


def mask_identifiers(text: str) -> str:
    return _IDENTIFIERS.sub(r"\1…\2", text)


class ErrorReporter:
    def __init__(
        self,
        *,
        quarantine_file: Path | None,
        limit_per_hour: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.quarantine_file = quarantine_file
        self.limit = limit_per_hour
        self.clock = clock
        self.counts: Counter[str] = Counter()
        self._logged: Counter[tuple[str | None, str]] = Counter()
        self._hour = self._current_hour()
        self._lock = threading.Lock()
        if quarantine_file:
            quarantine_file.parent.mkdir(parents=True, exist_ok=True)

    def _current_hour(self) -> str:
        return self.clock().strftime("%Y-%m-%dT%H")

    def report(
        self,
        category: Category,
        message: str,
        *,
        signature: str | None = None,
        meter: str | None = None,
        version: str | None = None,
        line: str | None = None,
        raw: bytes | str | None = None,
        quarantine: bool = False,
        count: int = 1,
    ) -> None:
        with self._lock:
            self._roll_hour()
            self.counts[category.value] += count
            key = (signature, category.value)
            self._logged[key] += 1
            full = self._logged[key] <= self.limit
            if category == Category.PAYLOAD_SHAPE:
                level = logging.DEBUG
            else:
                level = (
                    logging.WARNING
                    if category in (Category.FIELD, Category.MBUS_TIME)
                    else logging.ERROR
                )
            if full:
                parts = [f"category={category.value}", f"signature={signature or '-'}"]
                if meter or version:
                    parts.append(f"meter={meter or '-'} version={version or '-'}")
                parts.append(message)
                if line:
                    parts.append(f"line={mask_identifiers(line)!r}")
                error_log.log(level, " ".join(parts))
                if self._logged[key] == self.limit:
                    error_log.log(
                        level,
                        f"category={category.value} signature={signature or '-'} "
                        "limit reached; counting only until the next hour",
                    )
            if quarantine and self.quarantine_file:
                self._quarantine(category, message, signature, raw)

    def _quarantine(
        self, category: Category, message: str, signature: str | None, raw: bytes | str | None
    ) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "backslashreplace")
        record = {
            "time": self.clock().isoformat(),
            "category": category.value,
            "message": message,
            "signature": signature,
            "raw": mask_identifiers(raw) if raw is not None else None,
        }
        assert self.quarantine_file is not None
        try:
            with self.quarantine_file.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as error:
            log.error("cannot write quarantine file %s: %s", self.quarantine_file, error)

    def _roll_hour(self) -> None:
        hour = self._current_hour()
        if hour != self._hour:
            self._hour = hour
            self._logged.clear()

    def take_counts(self) -> Counter[str]:
        """Errors per category since the previous call (for the hourly summary)."""
        with self._lock:
            counts, self.counts = self.counts, Counter()
            return counts
