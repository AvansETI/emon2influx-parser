"""InfluxDB access for the counter correction, behind a protocol so tests can use a fake."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from .votes import DayStats

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
CORRECTIONS = "dsmr_corrections"
_SIGNATURE = re.compile(r"[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*-[0-9A-F]{6}-[0-9A-F]{6}")


@dataclass(frozen=True)
class EnergyPoint:
    time_ns: int
    consumed: float
    produced: float


@dataclass(frozen=True)
class Correction:
    signature: str
    start: datetime
    stop: datetime
    kind: str  # counters_swapped | restored
    recorded_at: datetime


class Store(Protocol):
    bucket: str

    def signatures(self, start: datetime, stop: datetime) -> list[str]: ...

    def daily_stats(self, signature: str, start: datetime, stop: datetime) -> list[DayStats]: ...

    def energy_points(
        self, signature: str, start: datetime, stop: datetime
    ) -> Iterator[EnergyPoint]: ...

    def raw_lines(
        self, signature: str, start: datetime, stop: datetime, fields: tuple[str, ...]
    ) -> Iterator[str]: ...

    def corrections(self, signature: str) -> list[Correction]: ...

    def write(self, lines: list[str]) -> None: ...


def checked(signature: str) -> str:
    """Signatures are inlined into Flux and line protocol; only well-formed ones pass."""
    if not _SIGNATURE.fullmatch(signature):
        raise ValueError(f"not a signature: {signature!r}")
    return signature


def local_day(moment: datetime) -> date:
    return moment.astimezone(AMSTERDAM).date()


def day_start(day: date) -> datetime:
    """00:00 Dutch time of `day`, in UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=AMSTERDAM).astimezone(UTC)


def day_chunks(start: datetime, stop: datetime) -> Iterator[tuple[datetime, datetime]]:
    """[start, stop) in pieces of one Dutch day, so large exports stay small in memory."""
    current = start
    while current < stop:
        nxt = min(stop, day_start(local_day(current) + timedelta(days=1)))
        yield current, nxt
        current = nxt


def energy_line(measurement: str, signature: str, point: EnergyPoint) -> str:
    return (
        f"{measurement},signature={checked(signature)} "
        f"energy_consumed={point.consumed!r},energy_produced={point.produced!r} "
        f"{point.time_ns}"
    )


class InfluxStore:
    def __init__(
        self, url: str, org: str, bucket: str, token: str, measurement: str = "dsmr"
    ) -> None:
        from influxdb_client.client.influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.bucket = bucket
        self.org = org
        self.measurement = measurement
        self.client = InfluxDBClient(url=url, token=token, org=org, timeout=300_000)
        self.query_api = self.client.query_api()
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

    def _range(self, start: datetime, stop: datetime) -> str:
        return (
            f'from(bucket: "{self.bucket}") |> range(start: {start.isoformat()}, '
            f"stop: {stop.isoformat()})"
        )

    def signatures(self, start: datetime, stop: datetime) -> list[str]:
        flux = (
            f'import "influxdata/influxdb/schema"\nschema.tagValues(bucket: "{self.bucket}", '
            f'tag: "signature", predicate: (r) => r._measurement == "{self.measurement}", '
            f"start: {start.isoformat()}, stop: {stop.isoformat()})"
        )
        values = [str(r.get_value()) for t in self.query_api.query(flux) for r in t.records]
        return sorted(v for v in values if _SIGNATURE.fullmatch(v))

    def daily_stats(self, signature: str, start: datetime, stop: datetime) -> list[DayStats]:
        """In blocks of 60 days: years in one query exceed the proxy's time-out."""
        result: list[DayStats] = []
        block_start = start
        while block_start < stop:
            block_stop = min(stop, day_start(local_day(block_start) + timedelta(days=60)))
            result.extend(self._daily_stats(signature, block_start, block_stop))
            block_start = block_stop
        return result

    def _daily_stats(self, signature: str, start: datetime, stop: datetime) -> list[DayStats]:
        sig = checked(signature)
        flux = f'''import "timezone"
option location = timezone.location(name: "Europe/Amsterdam")
data = {self._range(start, stop)}
  |> filter(fn: (r) => r._measurement == "{self.measurement}" and r.signature == "{sig}")
power = data |> filter(fn: (r) => r._field == "power_consumed")
power |> aggregateWindow(every: 1d, fn: count, createEmpty: false) |> yield(name: "count")
power
  |> aggregateWindow(
    every: 1d,
    fn: (column, tables=<-) => tables |> integral(unit: 1h, column: column),
    createEmpty: false,
  )
  |> yield(name: "integral")
data
  |> filter(fn: (r) => r._field == "energy_consumed" or r._field == "energy_produced")
  |> aggregateWindow(every: 1d, fn: spread, createEmpty: false)
  |> yield(name: "spread")
'''
        days: dict[date, dict[str, float]] = {}
        for table in self.query_api.query(flux):
            for record in table.records:
                # aggregateWindow stamps the window end: the day is the one before it.
                day = local_day(record.get_time() - timedelta(seconds=1))
                key = record.values.get("result")
                if key == "spread":
                    key = record.get_field()
                days.setdefault(day, {})[str(key)] = float(record.get_value())
        return [
            DayStats(
                day,
                int(v.get("count", 0)),
                v.get("integral"),
                v.get("energy_consumed"),
                v.get("energy_produced"),
            )
            for day, v in sorted(days.items())
        ]

    def energy_points(
        self, signature: str, start: datetime, stop: datetime
    ) -> Iterator[EnergyPoint]:
        sig = checked(signature)
        for chunk_start, chunk_stop in day_chunks(start, stop):
            flux = f'''{self._range(chunk_start, chunk_stop)}
  |> filter(fn: (r) => r._measurement == "{self.measurement}" and r.signature == "{sig}")
  |> filter(fn: (r) => r._field == "energy_consumed" or r._field == "energy_produced")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.energy_consumed and exists r.energy_produced)
'''
            for record in self.query_api.query_stream(flux):
                moment: datetime = record.get_time()
                ns = (moment - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000
                yield EnergyPoint(
                    ns, float(record["energy_consumed"]), float(record["energy_produced"])
                )

    def raw_lines(
        self, signature: str, start: datetime, stop: datetime, fields: tuple[str, ...]
    ) -> Iterator[str]:
        sig = checked(signature)
        field_filter = " or ".join(f'r._field == "{f}"' for f in fields)
        for chunk_start, chunk_stop in day_chunks(start, stop):
            flux = f'''{self._range(chunk_start, chunk_stop)}
  |> filter(fn: (r) => r._measurement == "{self.measurement}" and r.signature == "{sig}")
  |> filter(fn: (r) => {field_filter})
'''
            for record in self.query_api.query_stream(flux):
                moment: datetime = record.get_time()
                ns = (moment - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000
                value = record.get_value()
                if isinstance(value, float):
                    yield f"{self.measurement},signature={sig} {record.get_field()}={value!r} {ns}"

    def field_summary(
        self, field: str, start: datetime, stop: datetime
    ) -> dict[str, tuple[int, float]]:
        """Per signature: number of points and maximum of `field`, in blocks of 60 days."""
        summary: dict[str, tuple[int, float]] = {}
        block_start = start
        while block_start < stop:
            block_stop = min(stop, day_start(local_day(block_start) + timedelta(days=60)))
            base = f"""{self._range(block_start, block_stop)}
  |> filter(fn: (r) => r._measurement == "{self.measurement}" and r._field == "{field}")
  |> group(columns: ["signature"])"""
            counts = {
                str(r["signature"]): int(r.get_value())
                for t in self.query_api.query(base + "\n  |> count()")
                for r in t.records
            }
            maxima = {
                str(r["signature"]): float(r.get_value())
                for t in self.query_api.query(base + "\n  |> max()")
                for r in t.records
            }
            for sig, count in counts.items():
                old_count, old_max = summary.get(sig, (0, float("-inf")))
                summary[sig] = (old_count + count, max(old_max, maxima.get(sig, old_max)))
            block_start = block_stop
        return summary

    def point_lines(self, signature: str, start: datetime, stop: datetime) -> list[str]:
        """Every point of a household in [start, stop) with all its fields and types."""
        from influxdb_client.client.write.point import Point
        from influxdb_client.domain.write_precision import WritePrecision

        sig = checked(signature)
        flux = f"""{self._range(start, stop)}
  |> filter(fn: (r) => r._measurement == "{self.measurement}" and r.signature == "{sig}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        skip = {"result", "table", "_start", "_stop", "_time", "_measurement", "signature"}
        lines = []
        for table in self.query_api.query(flux):
            for record in table.records:
                point = Point(self.measurement).tag("signature", sig)
                for key, value in record.values.items():
                    if key not in skip and value is not None:
                        point = point.field(key, value)
                moment: datetime = record.get_time()
                ns = (moment - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000
                lines.append(point.time(ns, WritePrecision.NS).to_line_protocol())
        return lines

    def delete_points(self, signature: str, start: datetime, stop: datetime) -> None:
        """Deletes all points of a household in [start, stop). Influx's stop is inclusive."""
        sig = checked(signature)
        self.client.delete_api().delete(
            start,
            stop - timedelta(microseconds=1),
            f'_measurement="{self.measurement}" AND signature="{sig}"',
            bucket=self.bucket,
            org=self.org,
        )

    def corrections(self, signature: str) -> list[Correction]:
        sig = checked(signature)
        flux = f'''from(bucket: "{self.bucket}") |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "{CORRECTIONS}" and r.signature == "{sig}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
        result = []
        for table in self.query_api.query(flux):
            for r in table.records:
                result.append(
                    Correction(
                        sig,
                        datetime.fromisoformat(r["from"]),
                        datetime.fromisoformat(r["to"]),
                        str(r["kind"]),
                        r.get_time(),
                    )
                )
        return result

    def write(self, lines: list[str]) -> None:
        self.write_api.write(bucket=self.bucket, org=self.org, record=lines)

    def close(self) -> None:
        self.client.close()
