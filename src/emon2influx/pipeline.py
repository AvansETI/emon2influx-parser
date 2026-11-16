"""One MQTT message → line-protocol lines, with every problem reported (§6, §8)."""

from collections import Counter
from datetime import datetime

from .diagnostics import ErrorReporter
from .errors import Category, RejectedMessage
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
    ) -> None:
        self.decoders = decoders
        self.parser = parser
        self.builder = builder
        self.reporter = reporter
        self.stats: Counter[str] = Counter()
        self.kits: set[str] = set()
        self.unknown_codes: Counter[str] = Counter()

    def process(self, raw: bytes, received_at: datetime) -> list[str]:
        self.stats["messages"] += 1
        signature = None
        try:
            payload, signature = self.decoders.decode(raw, received_at)
            reading = self.parser.parse(payload.p1, received_at)
        except RejectedMessage as rejected:
            self.stats["rejected"] += 1
            self.reporter.report(
                rejected.category,
                rejected.message,
                signature=signature.value if signature else None,
                line=rejected.line,
                raw=raw,
                # The bare `1` and similar messages are expected noise.
                quarantine=rejected.category != Category.PAYLOAD_SHAPE,
            )
            return []

        self.kits.add(signature.value)
        for issue in reading.issues:
            self.reporter.report(
                Category(issue.category),
                issue.message,
                signature=signature.value,
                meter=reading.manufacturer,
                version=reading.version,
                line=issue.line,
            )
        self.unknown_codes.update(reading.unknown_codes)
        lines = self.builder.build(signature, reading)
        self.stats["points"] += len(lines)
        return lines
