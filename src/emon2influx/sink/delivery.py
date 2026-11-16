"""One attempt to write a batch, with its outcome counted and rejected data quarantined."""

from enum import Enum

from ..diagnostics import ErrorEvent, ErrorReporter
from ..errors import Category
from ..metrics import Metrics
from .writer import WriteError, Writer


class Outcome(Enum):
    WRITTEN = "written"
    RETRY = "retry"  # keep the batch and try again later
    REJECTED = "rejected"  # InfluxDB refuses the data itself; quarantined


class Delivery:
    def __init__(self, writer: Writer, metrics: Metrics, reporter: ErrorReporter) -> None:
        self.writer = writer
        self.metrics = metrics
        self.reporter = reporter
        self.last_error: WriteError | None = None

    def send(self, batch: list[str]) -> Outcome:
        try:
            if batch:
                self.writer.write(batch)
        except WriteError as error:
            self.last_error = error
            if error.retryable:
                return Outcome.RETRY
            self.metrics.incr("rejected_points", len(batch))
            event = ErrorEvent(Category.INFLUX_REJECTED, f"{error} ({len(batch)} points)")
            self.reporter.report(event, raw="\n".join(batch), quarantine=True, count=len(batch))
            return Outcome.REJECTED
        self.metrics.incr("written", len(batch))
        return Outcome.WRITTEN
