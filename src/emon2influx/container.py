"""Builds all components from the settings (doc/architecture.md §6). The only place that knows
which concrete classes are used; the rest receives its collaborators through the constructor."""

import queue
from dataclasses import dataclass

from . import build_version
from .config import Settings
from .diagnostics import ErrorEvent, ErrorReporter
from .errors import Category
from .fieldmap import FieldMapPublisher, FieldMapState
from .health import HealthReporter
from .metrics import Metrics
from .payload.registry import DecoderRegistry
from .pipeline import Pipeline
from .points import PointBuilder
from .sink.delivery import Delivery
from .sink.sink import InfluxSink
from .sink.spool import DiskSpool
from .sink.writer import DryRunWriter, InfluxWriter, Writer
from .source.mqtt import Message, MqttSource
from .summary import SummaryReporter
from .telegram.parser import TelegramParser

QUEUE_SIZE = 10_000


@dataclass
class Components:
    settings: Settings
    metrics: Metrics
    reporter: ErrorReporter
    messages: "queue.Queue[Message]"
    source: MqttSource
    pipeline: Pipeline
    spool: DiskSpool
    sink: InfluxSink
    summary: SummaryReporter
    field_map: FieldMapPublisher | None
    health: HealthReporter | None


def build(settings: Settings, writer: Writer | None = None) -> Components:
    metrics = Metrics()
    reporter = ErrorReporter.create(
        metrics,
        limit_per_hour=settings.error_log_limit_per_hour,
        quarantine_file=settings.quarantine_file,
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
    pipeline = Pipeline(
        DecoderRegistry(settings.signature_prefixes),
        TelegramParser(require_valid_crc=settings.require_valid_crc),
        PointBuilder(settings.influx_measurement),
        reporter,
        metrics,
    )
    spool = DiskSpool(settings.spool_dir, settings.spool_max_mb * 1024 * 1024)
    delivery = Delivery(writer, metrics, reporter)
    sink = InfluxSink(
        delivery,
        spool,
        metrics,
        reporter,
        batch_size=settings.influx_batch_size,
        flush_interval_s=settings.influx_flush_interval_ms / 1000,
    )
    messages: queue.Queue[Message] = queue.Queue(maxsize=QUEUE_SIZE)

    def dropped() -> None:
        metrics.incr("queue_dropped")
        reporter.report(
            ErrorEvent(Category.QUEUE_DROPPED, "parser could not keep up; oldest message dropped")
        )

    source = MqttSource(settings, messages, dropped)
    field_map = (
        FieldMapPublisher(
            writer, FieldMapState(settings.spool_dir.parent / "state" / "fieldmap.json")
        )
        if settings.mode == "write"
        else None
    )
    return Components(
        settings,
        metrics,
        reporter,
        messages,
        source,
        pipeline,
        spool,
        sink,
        SummaryReporter(metrics, spool),
        field_map,
        HealthReporter(
            settings.health_file,
            build_version(),
            mqtt_connected=source.is_connected,
            metrics=metrics,
            delivery=delivery,
            spool=spool,
        )
        if settings.health_file
        else None,
    )
