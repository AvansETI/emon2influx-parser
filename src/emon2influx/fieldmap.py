"""The `dsmr_fields` measurement: every field with its OBIS origin, in InfluxDB itself (§4.3).

Written at start-up when the registry changed since the last time, which is remembered in a
small state file next to the spool.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from influxdb_client.client.write.point import Point
from influxdb_client.domain.write_precision import WritePrecision

from .sink.writer import WriteError, Writer
from .telegram.registry import field_map, registry_hash

log = logging.getLogger("emon2influx")

MEASUREMENT = "dsmr_fields"


def field_map_lines(now: datetime | None = None) -> list[str]:
    moment = now or datetime.now(UTC)
    version = registry_hash()
    lines: list[str] = []
    for row in field_map():
        point = (
            Point(MEASUREMENT)
            .tag("field", str(row["field"]))
            .field("obis", ", ".join(str(code) for code in row["obis"]))  # type: ignore[attr-defined]
            .field("description", str(row["description"]))
            .field("unit", str(row["unit"] or ""))
            .field("type", str(row["type"]))
            .field("occurs", str(row["occurs"]))
            .field("point", str(row["point"]))
            .field("derived", bool(row.get("derived", False)))
            .field("registry", version)
            .time(moment, WritePrecision.NS)
        )
        lines.append(point.to_line_protocol())
    return lines


class FieldMapState:
    def __init__(self, path: Path) -> None:
        self.path = path

    def is_current(self) -> bool:
        try:
            return bool(json.loads(self.path.read_text())["registry"] == registry_hash())
        except (OSError, ValueError, KeyError):
            return False

    def mark_written(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"registry": registry_hash()}))


class FieldMapPublisher:
    """Writes `dsmr_fields` once per registry version."""

    def __init__(self, writer: Writer, state: FieldMapState) -> None:
        self.writer = writer
        self.state = state

    def publish(self) -> bool:
        """True when written now; False when current already or when writing failed."""
        if self.state.is_current():
            return False
        try:
            self.writer.write(field_map_lines())
        except WriteError as error:
            log.warning("could not write dsmr_fields (%s); trying again at the next start", error)
            return False
        self.state.mark_written()
        log.info("dsmr_fields written")
        return True
