"""Start, run and stop (doc/architecture.md §6). Building the components is the container's job;
processing is the worker's; this class only orders the lifecycle and handles signals."""

import logging
import signal
import threading
from datetime import UTC, datetime
from types import FrameType

from . import __version__
from .config import Settings
from .container import Components, build
from .logging_setup import setup_logging
from .worker import Worker

log = logging.getLogger("emon2influx")


class App:
    def __init__(self, components: Components) -> None:
        self.c = components
        self.worker = Worker(
            components.messages, components.pipeline, components.sink, components.summary
        )
        self.stop = threading.Event()

    def run(self) -> int:
        s = self.c.settings
        log.info(
            "emon2influx-parser %s starting: mode=%s bucket=%s measurement=%s prefixes=%s",
            __version__,
            s.mode,
            s.influx_bucket or "-",
            s.influx_measurement,
            ",".join(s.signature_prefixes),
        )
        if self.c.field_map:
            self.c.field_map.publish()
        self.c.sink.start()
        self.c.source.start()
        try:
            self.worker.run(self.stop)
        finally:
            log.info("stopping")
            self.c.source.stop()
            self.worker.drain()
            self.c.sink.close()
            self.c.summary.report()
            log.info("stopped; spool holds %d batch(es)", self.c.spool.count_files())
        return 0

    def request_stop(self, signum: int, frame: FrameType | None) -> None:
        log.info("signal %s received", signal.Signals(signum).name)
        self.stop.set()


def main_with(settings: Settings) -> int:
    setup_logging(settings)
    app = App(build(settings))
    signal.signal(signal.SIGTERM, app.request_stop)
    signal.signal(signal.SIGINT, app.request_stop)
    started = datetime.now(UTC)
    code = app.run()
    log.info("ran for %s", datetime.now(UTC) - started)
    return code
