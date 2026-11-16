"""Batched writes to InfluxDB with a disk spool for outages (doc/architecture.md §7).

New batches go to the spool while it is not empty, so points reach InfluxDB in order. Replay
is idempotent: InfluxDB overwrites a point with the same series and timestamp.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

from ..diagnostics import ErrorReporter
from ..errors import Category
from .spool import DiskSpool

log = logging.getLogger("emon2influx")

# Retried later (data kept): outages, overload and configuration problems that an operator
# can fix (token, bucket). Other 4xx responses mean the data itself is rejected.
RETRYABLE_STATUS = frozenset({401, 403, 404, 408, 429}) | frozenset(range(500, 600))


class WriteError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Writer(Protocol):
    def write(self, lines: list[str]) -> None: ...

    def close(self) -> None: ...


class InfluxWriter:
    def __init__(
        self, url: str, org: str, bucket: str, token: str, timeout_ms: int = 10_000
    ) -> None:
        from influxdb_client.client.influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.bucket = bucket
        self.org = org
        self.client = InfluxDBClient(
            url=url, token=token, org=org, timeout=timeout_ms, retries=False
        )
        self.api = self.client.write_api(write_options=SYNCHRONOUS)

    def write(self, lines: list[str]) -> None:
        from influxdb_client.rest import ApiException

        try:
            self.api.write(bucket=self.bucket, org=self.org, record=lines)
        except ApiException as error:
            status = int(error.status) if error.status else None
            raise WriteError(
                f"InfluxDB answered {status}: {str(error.body)[:300]}",
                retryable=status is None or status in RETRYABLE_STATUS,
                status=status,
            ) from None
        except Exception as error:  # connection refused, DNS, TLS, timeouts
            raise WriteError(f"InfluxDB unreachable: {error}", retryable=True) from None

    def close(self) -> None:
        self.client.close()


class DryRunWriter:
    """MODE=dry-run: accepts everything and writes nothing."""

    def __init__(self) -> None:
        self.lines = 0

    def write(self, lines: list[str]) -> None:
        self.lines += len(lines)
        for line in lines[:3]:
            log.debug("dry-run: %s", line)

    def close(self) -> None:
        pass


class InfluxSink:
    MIN_BACKOFF_S = 1.0
    MAX_BACKOFF_S = 300.0

    def __init__(
        self,
        writer: Writer,
        spool: DiskSpool,
        reporter: ErrorReporter,
        *,
        batch_size: int,
        flush_interval_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.writer = writer
        self.spool = spool
        self.reporter = reporter
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.clock = clock
        self._pending: list[str] = []
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._backoff = 0.0
        self._next_attempt = 0.0
        self.outage_since: float | None = None
        self.stats = {"written": 0, "spooled": 0, "dropped": 0, "rejected": 0}

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
        self._next_attempt = 0.0
        self.tick(final=True)
        self.writer.close()

    def tick(self, *, final: bool = False) -> None:
        self._replay(budget_s=5.0 if final else 2.0)
        while True:
            with self._cond:
                batch, self._pending = (
                    self._pending[: self.batch_size],
                    self._pending[self.batch_size :],
                )
            if not batch:
                return
            if not self.spool.is_empty() or self.clock() < self._next_attempt:
                self._to_spool(batch)
            else:
                self._send(batch)

    def _send(self, batch: list[str]) -> None:
        try:
            self.writer.write(batch)
        except WriteError as error:
            if error.retryable:
                self._failed(error)
                self._to_spool(batch)
            else:
                self._rejected(batch, error)
            return
        self.stats["written"] += len(batch)
        self._recovered()

    def _replay(self, budget_s: float) -> None:
        deadline = self.clock() + budget_s
        while self.clock() < deadline and self.clock() >= self._next_attempt:
            path = self.spool.oldest()
            if path is None:
                self._recovered()
                return
            batch = self.spool.read(path)
            try:
                if batch:
                    self.writer.write(batch)
            except WriteError as error:
                if error.retryable:
                    self._failed(error)
                    return
                self._rejected(batch, error)
            else:
                self.stats["written"] += len(batch)
                self._backoff = 0.0
            self.spool.remove(path)

    def _to_spool(self, batch: list[str]) -> None:
        dropped = self.spool.append(batch)
        self.stats["spooled"] += len(batch)
        if dropped:
            self.stats["dropped"] += dropped
            self.reporter.report(
                Category.SPOOL_DROPPED,
                f"spool full; dropped the {dropped} oldest point(s)",
                count=dropped,
            )

    def _failed(self, error: WriteError) -> None:
        self._backoff = min(self.MAX_BACKOFF_S, max(self.MIN_BACKOFF_S, self._backoff * 2))
        self._next_attempt = self.clock() + self._backoff
        if self.outage_since is None:
            self.outage_since = self.clock()
            log.error("%s; buffering to the spool and retrying", error)
        else:
            log.debug("%s; next attempt in %.0f s", error, self._backoff)

    def _recovered(self) -> None:
        if self.outage_since is not None and self.spool.is_empty():
            log.info(
                "InfluxDB is writable again after %.0f s; spool replayed",
                self.clock() - self.outage_since,
            )
            self.outage_since = None
        self._backoff = 0.0

    def _rejected(self, batch: list[str], error: WriteError) -> None:
        self.stats["rejected"] += len(batch)
        self.reporter.report(
            Category.INFLUX_REJECTED,
            f"{error} ({len(batch)} points)",
            raw="\n".join(batch),
            quarantine=True,
            count=len(batch),
        )
