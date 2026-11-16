"""Telegram → Reading. Fields are recognised by OBIS code, not by a parser per DSMR version, so
import is always 1.8.x and export always 2.8.x (doc/architecture.md §3.4, §4).

A line that cannot be read is skipped and reported as a `FieldIssue`; the rest of the telegram
is kept. Only a broken frame or CRC rejects the whole telegram.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .frame import TelegramFrame
from .obis import ObisLine, ObisValue, join_continuations, parse_line
from .registry import BY_OBIS, EXCLUDED, MBUS_BY_TYPE, MBUS_READINGS, SOURCE_ONLY, FieldSpec
from .timestamp import parse_meter_time

Value = float | int | str

# A capture time further than this from the receipt time is not trusted (§5).
MAX_CAPTURE_SKEW = timedelta(hours=2)


@dataclass(frozen=True)
class FieldIssue:
    category: str
    message: str
    line: str | None = None


@dataclass(frozen=True)
class MbusReading:
    field: str
    value: float
    captured_at: datetime
    channel: int


@dataclass
class Reading:
    received_at: datetime
    manufacturer: str | None
    version: str | None
    meter_time: datetime | None
    electricity: dict[str, Value] = field(default_factory=dict)
    mbus: list[MbusReading] = field(default_factory=list)
    issues: list[FieldIssue] = field(default_factory=list)
    unknown_codes: list[str] = field(default_factory=list)


class TelegramParser:
    def __init__(self, *, require_valid_crc: bool = True) -> None:
        self.require_valid_crc = require_valid_crc

    def parse(self, text: str, received_at: datetime) -> Reading:
        frame = TelegramFrame.parse(text, require_valid_crc=self.require_valid_crc)
        reading = Reading(received_at, frame.manufacturer, None, None)

        lines: dict[str, ObisLine] = {}
        mbus: dict[int, list[ObisLine]] = {}
        for raw in join_continuations(frame.lines):
            line = parse_line(raw)
            if line is None:
                reading.issues.append(FieldIssue("field", "unreadable line", raw))
                continue
            if line.medium == 0 and line.channel > 0:
                mbus.setdefault(line.channel, []).append(line)
            elif line.code in lines:
                reading.issues.append(FieldIssue("field", "duplicate line ignored", raw))
            else:
                lines[line.code] = line

        reading.version = _version(lines)
        reading.meter_time = self._meter_time(lines, reading)
        self._electricity(lines, reading)
        self._totals(lines, reading)
        self._mbus(mbus, reading)
        if frame.manufacturer:
            reading.electricity["meter_manufacturer"] = frame.manufacturer
        if reading.version:
            reading.electricity["dsmr_version"] = reading.version
        return reading

    def _meter_time(self, lines: dict[str, ObisLine], reading: Reading) -> datetime | None:
        line = lines.get("0-0:1.0.0")
        if line is None or not line.values:
            return None
        moment = parse_meter_time(line.values[0].text)
        if moment is None:
            reading.issues.append(FieldIssue("field", "unreadable meter time", line.raw))
            return None
        reading.electricity["clock_offset_s"] = round(
            (reading.received_at - moment).total_seconds()
        )
        return moment

    def _electricity(self, lines: dict[str, ObisLine], reading: Reading) -> None:
        for code, line in lines.items():
            spec = BY_OBIS.get(code)
            if spec is None:
                if code not in SOURCE_ONLY and code not in EXCLUDED and code != "0-0:96.1.4":
                    reading.unknown_codes.append(code)
                continue
            value = _convert(spec, line, reading)
            if value is not None:
                reading.electricity[spec.field] = value

    def _totals(self, lines: dict[str, ObisLine], reading: Reading) -> None:
        for direction, total_code in (("consumed", "1-0:1.8.0"), ("produced", "1-0:2.8.0")):
            t1 = reading.electricity.get(f"energy_{direction}_t1")
            t2 = reading.electricity.get(f"energy_{direction}_t2")
            if isinstance(t1, float) and isinstance(t2, float):
                reading.electricity[f"energy_{direction}"] = round(t1 + t2, 6)
            elif total_code in lines:
                spec = FieldSpec(
                    total_code,
                    f"energy_{direction}",
                    "kWh",
                    "float",
                    "",
                    "",
                    BY_OBIS["1-0:1.8.1"].units,
                )
                value = _convert(spec, lines[total_code], reading)
                if value is not None:
                    reading.electricity[f"energy_{direction}"] = value

    def _mbus(self, channels: dict[int, list[ObisLine]], reading: Reading) -> None:
        taken: set[str] = set()
        for channel, lines in sorted(channels.items()):
            device_type: int | None = None
            values: list[ObisLine] = []
            for line in lines:
                if line.item == "24.1.0" and line.values:
                    try:
                        device_type = int(line.values[0].text)
                    except ValueError:
                        reading.issues.append(
                            FieldIssue("field", "unreadable device type", line.raw)
                        )
                elif line.generic_code in MBUS_READINGS:
                    values.append(line)
                elif line.generic_code not in EXCLUDED and line.generic_code not in SOURCE_ONLY:
                    reading.unknown_codes.append(line.generic_code)
            for line in values:
                parsed = _mbus_value(line)
                if parsed is None:
                    reading.issues.append(FieldIssue("field", "unreadable M-Bus reading", line.raw))
                    continue
                captured_text, number, unit = parsed
                spec = MBUS_BY_TYPE.get(device_type) if device_type is not None else None
                # Old meters give no device type; a reading in m3 there is gas.
                if spec is None and device_type is None and unit == "m3":
                    spec = MBUS_BY_TYPE[3]
                if spec is None:
                    reading.issues.append(
                        FieldIssue(
                            "field", f"unsupported M-Bus device type {device_type}", line.raw
                        )
                    )
                    continue
                if spec.field in taken:
                    reading.issues.append(
                        FieldIssue("field", f"second {spec.field} channel ignored", line.raw)
                    )
                    continue
                factor = spec.units.get(unit or "")
                if factor is None:
                    reading.issues.append(
                        FieldIssue("field", f"unexpected unit {unit!r}", line.raw)
                    )
                    continue
                captured = parse_meter_time(captured_text)
                if captured is None or abs(captured - reading.received_at) > MAX_CAPTURE_SKEW:
                    reading.issues.append(
                        FieldIssue(
                            "mbus_time", "capture time not trusted; receipt time used", line.raw
                        )
                    )
                    captured = reading.received_at
                taken.add(spec.field)
                reading.mbus.append(
                    MbusReading(spec.field, round(number * factor, 6), captured, channel)
                )


def _version(lines: dict[str, ObisLine]) -> str | None:
    nl = lines.get("1-3:0.2.8")
    if nl and nl.values and nl.values[0].text.strip():
        return nl.values[0].text.strip()
    be = lines.get("0-0:96.1.4")
    if be and be.values and be.values[0].text.strip():
        return "BE" + be.values[0].text.strip()
    return None


def _convert(spec: FieldSpec, line: ObisLine, reading: Reading) -> Value | None:
    if not line.values:
        reading.issues.append(FieldIssue("field", f"{spec.field}: no value", line.raw))
        return None
    value: ObisValue = line.values[-1]
    if spec.units:
        factor = spec.units.get(value.unit or "")
        if factor is None:
            reading.issues.append(
                FieldIssue("field", f"{spec.field}: unexpected unit {value.unit!r}", line.raw)
            )
            return None
    elif value.unit is not None:
        reading.issues.append(
            FieldIssue("field", f"{spec.field}: unexpected unit {value.unit!r}", line.raw)
        )
        return None
    else:
        factor = 1.0
    try:
        if spec.kind == "int":
            return int(value.text)
        return round(float(value.text) * factor, 6)
    except ValueError:
        reading.issues.append(
            FieldIssue("field", f"{spec.field}: unreadable value {value.text!r}", line.raw)
        )
        return None


def _mbus_value(line: ObisLine) -> tuple[str, float, str | None] | None:
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
