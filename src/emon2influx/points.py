"""Reading → InfluxDB line protocol (doc/architecture.md §4). Escaping is done by the client
library; the signature is the only tag."""

from datetime import UTC, datetime, timedelta

from influxdb_client.client.write.point import Point
from influxdb_client.domain.write_precision import WritePrecision

from .signature import Signature
from .telegram.parser import Reading

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _ns(moment: datetime) -> int:
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000


class PointBuilder:
    def __init__(self, measurement: str) -> None:
        self.measurement = measurement

    def build(self, signature: Signature, reading: Reading) -> list[str]:
        lines: list[str] = []
        # Identification fields alone are not a measurement.
        if any(k not in ("meter_manufacturer", "dsmr_version") for k in reading.electricity):
            point = Point(self.measurement).tag("signature", signature.value)
            for name, value in sorted(reading.electricity.items()):
                point = point.field(name, value)
            lines.append(point.time(_ns(reading.received_at), WritePrecision.NS).to_line_protocol())
        for mbus in reading.mbus:
            point = (
                Point(self.measurement)
                .tag("signature", signature.value)
                .field(mbus.field, float(mbus.value))
                .time(_ns(mbus.captured_at), WritePrecision.NS)
            )
            lines.append(point.to_line_protocol())
        return lines
