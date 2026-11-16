"""One MQTT message → line-protocol lines, with every problem reported (§6, §8)."""

from datetime import datetime

from .diagnostics import ErrorEvent, ErrorReporter
from .errors import Category, RejectedMessage
from .metrics import Metrics
from .payload.registry import DecoderRegistry
from .points import PointBuilder
from .telegram.parser import TelegramParser


class Pipeline:
    def __init__(
        self,
        decoders: DecoderRegistry,
        parser: TelegramParser,
        builder: PointBuilder,
        reporter: ErrorReporter,
        metrics: Metrics,
    ) -> None:
        self.decoders = decoders
        self.parser = parser
        self.builder = builder
        self.reporter = reporter
        self.metrics = metrics

    def process(self, raw: bytes, received_at: datetime) -> list[str]:
        self.metrics.incr("messages")
        signature = None
        try:
            payload, signature = self.decoders.decode(raw, received_at)
            reading = self.parser.parse(payload.p1, received_at)
        except RejectedMessage as rejected:
            self.metrics.incr("rejected")
            event = ErrorEvent(
                rejected.category,
                rejected.message,
                signature=signature.value if signature else None,
                line=rejected.line,
            )
            # The bare `1` and similar messages are expected noise.
            self.reporter.report(
                event, raw=raw, quarantine=rejected.category != Category.PAYLOAD_SHAPE
            )
            return []

        self.metrics.kit(signature.value)
        for issue in reading.issues:
            event = ErrorEvent(
                Category(issue.category),
                issue.message,
                signature=signature.value,
                meter=reading.manufacturer,
                version=reading.version,
                line=issue.line,
            )
            self.reporter.report(event, written=True)
        self.metrics.unknown_codes(reading.unknown_codes)
        lines = self.builder.build(signature, reading)
        self.metrics.incr("points", len(lines))
        return lines
