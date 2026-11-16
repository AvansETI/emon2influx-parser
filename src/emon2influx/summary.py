"""The hourly summary in the main log (doc/architecture.md §8)."""

import logging
import time
from collections.abc import Callable

from .metrics import Metrics
from .sink.spool import DiskSpool

log = logging.getLogger("emon2influx")

SUMMARY_EVERY_S = 3600.0


def _join(items: list[tuple[str, int]]) -> str:
    return ", ".join(f"{key}: {value}" for key, value in items)


class SummaryReporter:
    def __init__(
        self,
        metrics: Metrics,
        spool: DiskSpool,
        *,
        every_s: float = SUMMARY_EVERY_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.metrics = metrics
        self.spool = spool
        self.every_s = every_s
        self.clock = clock
        self._last = clock()

    def maybe_report(self) -> None:
        if self.clock() - self._last >= self.every_s:
            self.report()

    def report(self) -> str:
        self._last = self.clock()
        snap = self.metrics.snapshot(reset=True)
        c = snap.counters
        line = (
            f"summary: messages={c['messages']} kits={len(snap.kits)} points={c['points']} "
            f"rejected={c['rejected']} written={c['written']} spooled={c['spooled']} "
            f"dropped={c['dropped'] + c['queue_dropped']} "
            f"spool={self.spool.count_files()} batch(es)/{self.spool.size_bytes / 1e6:.1f} MB "
            f"errors={{{_join(sorted(snap.errors.items()))}}} "
            f"unknown_obis={{{_join(snap.unknown_codes.most_common(10))}}}"
        )
        log.info(line)
        return line
