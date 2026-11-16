"""Batches points and sends them to InfluxDB; during an outage they go to the disk spool and
are replayed later (doc/architecture.md §7).

This class only coordinates: `Delivery` makes one attempt, `BackoffPolicy` decides when to try
again, `SpoolReplayer` empties the spool and `DiskSpool` stores. New batches go to the spool
while it is not empty, so points reach InfluxDB in order.
"""

import logging
import threading
import time
from collections.abc import Callable

from ..diagnostics import ErrorEvent, ErrorReporter
from ..errors import Category
from ..metrics import Metrics
from .backoff import BackoffPolicy
from .delivery import Delivery, Outcome
from .replay import SpoolReplayer
from .spool import DiskSpool

log = logging.getLogger("emon2influx")


class InfluxSink:
    def __init__(
        self,
        delivery: Delivery,
        spool: DiskSpool,
        metrics: Metrics,
        reporter: ErrorReporter,
        *,
        batch_size: int,
        flush_interval_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.delivery = delivery
        self.spool = spool
        self.metrics = metrics
        self.reporter = reporter
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.clock = clock
        self.backoff = BackoffPolicy(clock=clock)
        self.replayer = SpoolReplayer(spool, delivery, self.backoff, clock)
        self.outage_since: float | None = None
        self._pending: list[str] = []
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- producer side ---------------------------------------------------------------------

    def add(self, lines: list[str]) -> None:
        if not lines:
            return
        with self._cond:
            self._pending.extend(lines)
            if len(self._pending) >= self.batch_size:
                self._cond.notify()

    # --- background thread -----------------------------------------------------------------

    def start(self) -> None:
        if not self.spool.is_empty():
            log.info(
                "spool holds %d batch(es) from before the start; replaying first",
                self.spool.count_files(),
            )
        self._thread = threading.Thread(target=self._run, name="influx-sink", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                if len(self._pending) < self.batch_size:
                    self._cond.wait(self.flush_interval_s)
            try:
                self.tick()
            except Exception:  # never let the writer thread die
                log.exception("unexpected error in the InfluxDB writer")

    def close(self) -> None:
        """Stops the thread and makes one last attempt; whatever is left goes to the spool."""
        self._stop.set()
        with self._cond:
            self._cond.notify()
        if self._thread:
            self._thread.join(timeout=30)
        self.backoff.allow_now()
        self.tick(final=True)
        self.delivery.writer.close()

    def tick(self, *, final: bool = False) -> None:
        empty = self.replayer.replay(budget_s=5.0 if final else 2.0)
        if not empty and self.delivery.last_error and self.outage_since is None:
            self._outage_started()
        while batch := self._take_batch():
            if not self.spool.is_empty() or not self.backoff.ready():
                self._to_spool(batch)
                continue
            outcome = self.delivery.send(batch)
            if outcome is Outcome.RETRY:
                self.backoff.failed()
                self._outage_started()
                self._to_spool(batch)
            else:
                self.backoff.succeeded()
        if self.outage_since is not None and self.spool.is_empty() and self.backoff.ready():
            log.info(
                "InfluxDB is writable again after %.0f s; spool replayed",
                self.clock() - self.outage_since,
            )
            self.outage_since = None

    def _take_batch(self) -> list[str]:
        with self._cond:
            batch, self._pending = (
                self._pending[: self.batch_size],
                self._pending[self.batch_size :],
            )
        return batch

    def _outage_started(self) -> None:
        if self.outage_since is None:
            self.outage_since = self.clock()
            log.error("%s; buffering to the spool and retrying", self.delivery.last_error)

    def _to_spool(self, batch: list[str]) -> None:
        dropped = self.spool.append(batch)
        self.metrics.incr("spooled", len(batch))
        if dropped:
            self.metrics.incr("dropped", dropped)
            event = ErrorEvent(
                Category.SPOOL_DROPPED, f"spool full; dropped the {dropped} oldest point(s)"
            )
            self.reporter.report(event, count=dropped)
