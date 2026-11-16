"""M-Bus meters (gas, water, heat) on channels `0-n:` (doc/architecture.md §4.2, §5)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .model import FieldIssue, MbusReading
from .obis import ObisLine
from .registry import EXCLUDED, MBUS_BY_TYPE, MBUS_READINGS, SOURCE_ONLY, MbusSpec
from .timestamp import parse_meter_time

# A capture time further than this from the receipt time is not trusted (§5).
MAX_CAPTURE_SKEW = timedelta(hours=2)


@dataclass
class MbusChannel:
    """The lines of one channel: its device type and its reading lines."""

    channel: int
    device_type: int | None = None
    readings: list[ObisLine] = field(default_factory=list)
    issues: list[FieldIssue] = field(default_factory=list)
    unknown_codes: list[str] = field(default_factory=list)

    @classmethod
    def from_lines(cls, channel: int, lines: list[ObisLine]) -> "MbusChannel":
        result = cls(channel)
        for line in lines:
            if line.item == "24.1.0" and line.values:
                try:
                    result.device_type = int(line.values[0].text)
                except ValueError:
                    result.issues.append(FieldIssue("field", "unreadable device type", line.raw))
            elif line.generic_code in MBUS_READINGS:
                result.readings.append(line)
            elif line.generic_code not in EXCLUDED and line.generic_code not in SOURCE_ONLY:
                result.unknown_codes.append(line.generic_code)
        return result

    def spec_for(self, unit: str | None) -> MbusSpec | None:
        if self.device_type is not None:
            return MBUS_BY_TYPE.get(self.device_type)
        # Old meters give no device type; a reading in m3 there is gas.
        return MBUS_BY_TYPE[3] if unit == "m3" else None


def reading_parts(line: ObisLine) -> tuple[str, float, str | None] | None:
    """(capture time, value, unit) for the three reading forms."""
    values = line.values
    try:
        if line.item == "24.3.0":
            # DSMR 2.2/3: (time)(00)(60)(1)(0-n:24.2.1)(m3)(value), value on the next line.
            if len(values) < 3:
                return None
            return values[0].text, float(values[-1].text), values[-2].text or None
        if len(values) < 2:
            return None
        return values[0].text, float(values[-1].text), values[-1].unit
    except ValueError:
        return None


class MbusMapper:
    """Channels → readings at their capture time; one channel per kind of meter."""

    def __init__(self, max_capture_skew: timedelta = MAX_CAPTURE_SKEW) -> None:
        self.max_capture_skew = max_capture_skew

    def map(
        self, channels: list[MbusChannel], received_at: datetime
    ) -> tuple[list[MbusReading], list[FieldIssue]]:
        readings: list[MbusReading] = []
        issues: list[FieldIssue] = []
        taken: set[str] = set()
        for channel in sorted(channels, key=lambda c: c.channel):
            issues.extend(channel.issues)
            for line in channel.readings:
                reading, issue = self._reading(channel, line, received_at, taken)
                if issue:
                    issues.append(issue)
                if reading:
                    taken.add(reading.field)
                    readings.append(reading)
        return readings, issues

    def _reading(
        self, channel: MbusChannel, line: ObisLine, received_at: datetime, taken: set[str]
    ) -> tuple[MbusReading | None, FieldIssue | None]:
        parts = reading_parts(line)
        if parts is None:
            return None, FieldIssue("field", "unreadable M-Bus reading", line.raw)
        captured_text, number, unit = parts
        spec = channel.spec_for(unit)
        if spec is None:
            message = f"unsupported M-Bus device type {channel.device_type}"
            return None, FieldIssue("field", message, line.raw)
        if spec.field in taken:
            return None, FieldIssue("field", f"second {spec.field} channel ignored", line.raw)
        factor = spec.units.get(unit or "")
        if factor is None:
            return None, FieldIssue("field", f"unexpected unit {unit!r}", line.raw)
        captured = parse_meter_time(captured_text)
        issue = None
        if captured is None or abs(captured - received_at) > self.max_capture_skew:
            issue = FieldIssue("mbus_time", "capture time not trusted; receipt time used", line.raw)
            captured = received_at
        reading = MbusReading(spec.field, round(number * factor, 6), captured, channel.channel)
        return reading, issue
