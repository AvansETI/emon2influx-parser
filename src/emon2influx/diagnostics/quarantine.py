"""Rejected messages as JSON Lines, with identifiers removed (doc/architecture.md §8)."""

import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .events import ErrorEvent

log = logging.getLogger("emon2influx")

# Identifier values that never leave the process, not even in the quarantine file (§4.4).
_IDENTIFIERS = re.compile(r"(0-\d+:96\.1\.[01]\(|0-0:96\.13\.[01]\()[^)]*(\))")


def mask_identifiers(text: str) -> str:
    return _IDENTIFIERS.sub(r"\1…\2", text)


class QuarantineStore:
    def __init__(
        self, path: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, event: ErrorEvent, raw: bytes | str | None) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "backslashreplace")
        record = {
            "time": self.clock().isoformat(),
            "category": event.category.value,
            "message": event.message,
            "signature": event.signature,
            "raw": mask_identifiers(raw) if raw is not None else None,
        }
        try:
            with self._lock, self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as error:
            log.error("cannot write quarantine file %s: %s", self.path, error)
