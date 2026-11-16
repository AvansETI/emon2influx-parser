"""`health.json`: the parser's status for the updater's health check (doc/architecture.md §11.1).

The parser has no HTTP endpoint; this file, rewritten atomically every 30 s, takes its place.
"""

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .metrics import Metrics
from .sink.delivery import Delivery
from .sink.spool import DiskSpool

log = logging.getLogger("emon2influx")

HEALTH_EVERY_S = 30.0


class HealthReporter:
    def __init__(
        self,
        path: Path,
        version: str,
        *,
        mqtt_connected: Callable[[], bool],
        metrics: Metrics,
        delivery: Delivery,
        spool: DiskSpool,
        every_s: float = HEALTH_EVERY_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.version = version
        self.mqtt_connected = mqtt_connected
        self.metrics = metrics
        self.delivery = delivery
        self.spool = spool
        self.every_s = every_s
        self.clock = clock
        self.started_at = datetime.now(UTC)
        self._last: float | None = None

    def maybe_write(self) -> None:
        if self._last is None or self.clock() - self._last >= self.every_s:
            self.write()

    def status(self) -> dict[str, object]:
        written = self.delivery.last_written_at
        return {
            "version": self.version,
            "started_at": self.started_at.isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
            "mqtt_connected": self.mqtt_connected(),
            "messages": self.metrics.total("messages"),
            "last_written_at": written.isoformat() if written else None,
            "spool_files": self.spool.count_files(),
        }

    def write(self) -> None:
        self._last = self.clock()
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.status(), indent=2) + "\n")
            tmp.replace(self.path)
        except OSError as error:
            log.warning("cannot write %s: %s", self.path, error)
