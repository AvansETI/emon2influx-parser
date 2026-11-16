"""Wiring, the worker loop, the hourly summary and a clean shutdown (doc/architecture.md §6)."""

import logging
import queue
import signal
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from types import FrameType

from . import __version__
from .config import Settings
from .diagnostics import ErrorReporter
from .errors import Category
from .fieldmap import FieldMapState, field_map_lines
from .payload.registry import DecoderRegistry
from .pipeline import Pipeline
from .points import PointBuilder
from .sink.influx import DryRunWriter, InfluxSink, InfluxWriter, WriteError, Writer
from .sink.spool import DiskSpool
from .source.mqtt import Message, MqttSource
from .telegram.parser import TelegramParser

log = logging.getLogger("emon2influx")

QUEUE_SIZE = 10_000
SUMMARY_EVERY_S = 3600


def setup_logging(settings: Settings) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger("emon2influx")
    root.setLevel(settings.log_level)
    root.handlers.clear()
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)
    if settings.log_dir:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        main = TimedRotatingFileHandler(
            settings.log_dir / "emon2influx-parser.log",
            when="H",
            backupCount=settings.log_keep,
            utc=True,
        )
        main.setFormatter(fmt)
        root.addHandler(main)
        errors = TimedRotatingFileHandler(
            settings.log_dir / "errors.log", when="H", backupCount=settings.log_keep, utc=True
        )
        errors.setFormatter(fmt)
        errors.setLevel(logging.DEBUG)
        error_logger = logging.getLogger("emon2influx.errors")
        error_logger.addHandler(errors)


class App:
    def __init__(self, settings: Settings, writer: Writer | None = None) -> None:
        self.settings = settings
        self.reporter = ErrorReporter(
            quarantine_file=settings.quarantine_file,
            limit_per_hour=settings.error_log_limit_per_hour,
        )
        self.pipeline = Pipeline(
            DecoderRegistry(settings.signature_prefixes),
            TelegramParser(require_valid_crc=settings.require_valid_crc),
            PointBuilder(settings.influx_measurement),
            self.reporter,
        )
        if writer is None:
            writer = (
                InfluxWriter(
                    settings.influx_url,
                    settings.influx_org,
                    settings.influx_bucket,
                    settings.influx_token,
                )
                if settings.mode == "write"
                else DryRunWriter()
            )
        self.writer = writer
        self.spool = DiskSpool(settings.spool_dir, settings.spool_max_mb * 1024 * 1024)
        self.sink = InfluxSink(
            writer,
            self.spool,
            self.reporter,
            batch_size=settings.influx_batch_size,
            flush_interval_s=settings.influx_flush_interval_ms / 1000,
        )
        self.messages: queue.Queue[Message] = queue.Queue(maxsize=QUEUE_SIZE)
        self.queue_dropped = 0
        self.source = MqttSource(settings, self.messages, self._dropped)
        self.stop = threading.Event()
        self._last_summary = time.monotonic()

    def _dropped(self) -> None:
        self.queue_dropped += 1

    def run(self) -> int:
        s = self.settings
        log.info(
            "emon2influx-parser %s starting: mode=%s bucket=%s measurement=%s prefixes=%s",
            __version__,
            s.mode,
            s.influx_bucket or "-",
            s.influx_measurement,
            ",".join(s.signature_prefixes),
        )
        self._write_field_map()
        self.sink.start()
        self.source.start()
        try:
            while not self.stop.is_set():
                try:
                    raw, received_at = self.messages.get(timeout=1.0)
                except queue.Empty:
                    pass
                else:
                    self.sink.add(self.pipeline.process(raw, received_at))
                self._maybe_summary()
        finally:
            log.info("stopping")
            self.source.stop()
            while True:  # finish what was already received
                try:
                    raw, received_at = self.messages.get_nowait()
                except queue.Empty:
                    break
                self.sink.add(self.pipeline.process(raw, received_at))
            self.sink.close()
            self._summary()
            log.info("stopped; spool holds %d batch(es)", self.spool.count_files())
        return 0

    def request_stop(self, signum: int, frame: FrameType | None) -> None:
        log.info("signal %s received", signal.Signals(signum).name)
        self.stop.set()

    def _write_field_map(self) -> None:
        if self.settings.mode != "write":
            return
        state = FieldMapState(self.settings.spool_dir.parent / "state" / "fieldmap.json")
        if state.is_current():
            return
        try:
            self.writer.write(field_map_lines())
        except WriteError as error:
            log.warning("could not write dsmr_fields (%s); trying again at the next start", error)
            return
        state.mark_written()
        log.info("dsmr_fields written")

    def _maybe_summary(self) -> None:
        if time.monotonic() - self._last_summary >= SUMMARY_EVERY_S:
            self._summary()

    def _summary(self) -> None:
        self._last_summary = time.monotonic()
        stats, self.pipeline.stats = self.pipeline.stats, Counter()
        kits, self.pipeline.kits = self.pipeline.kits, set()
        unknown, self.pipeline.unknown_codes = self.pipeline.unknown_codes, Counter()
        if self.queue_dropped:
            self.reporter.report(
                Category.QUEUE_DROPPED, "parser could not keep up", count=self.queue_dropped
            )
            self.queue_dropped = 0
        errors = self.reporter.take_counts()
        sink = dict(self.sink.stats)
        for key in self.sink.stats:
            self.sink.stats[key] = 0
        log.info(
            "summary: messages=%d kits=%d points=%d rejected=%d written=%d spooled=%d "
            "spool=%d batch(es)/%.1f MB errors={%s} unknown_obis={%s}",
            stats["messages"],
            len(kits),
            stats["points"],
            stats["rejected"],
            sink["written"],
            sink["spooled"],
            self.spool.count_files(),
            self.spool.size_bytes / 1e6,
            ", ".join(f"{k}: {v}" for k, v in sorted(errors.items())),
            ", ".join(f"{k}: {v}" for k, v in unknown.most_common(10)),
        )


def main_with(settings: Settings) -> int:
    setup_logging(settings)
    app = App(settings)
    signal.signal(signal.SIGTERM, app.request_stop)
    signal.signal(signal.SIGINT, app.request_stop)
    started = datetime.now(UTC)
    code = app.run()
    log.info("ran for %s", datetime.now(UTC) - started)
    return code
