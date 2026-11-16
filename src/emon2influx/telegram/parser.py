"""Telegram → Reading. Fields are recognised by OBIS code, not by a parser per DSMR version, so
import is always 1.8.x and export always 2.8.x (doc/architecture.md §3.4, §4).

The parser only coordinates: the frame checks the outer structure and CRC, the line index sorts
the lines, and the electricity mapper, the derived-field functions and the M-Bus mapper each
fill their part. A line that cannot be read becomes a `FieldIssue`; the rest is kept.
"""

from datetime import datetime

from .derived import DeriveContext, derive_all
from .electricity import ElectricityMapper
from .frame import TelegramFrame
from .lines import LineIndex
from .mbus import MbusChannel, MbusMapper
from .model import FieldIssue, MbusReading, Reading, Value
from .timestamp import parse_meter_time

__all__ = ["FieldIssue", "MbusReading", "Reading", "TelegramParser", "Value"]


class TelegramParser:
    def __init__(
        self,
        *,
        require_valid_crc: bool = False,
        electricity: ElectricityMapper | None = None,
        mbus: MbusMapper | None = None,
    ) -> None:
        self.require_valid_crc = require_valid_crc
        self.electricity = electricity or ElectricityMapper()
        self.mbus = mbus or MbusMapper()

    def parse(self, text: str, received_at: datetime) -> Reading:
        frame = TelegramFrame.parse(text, require_valid_crc=self.require_valid_crc)
        index = LineIndex.build(frame.lines)
        reading = Reading(received_at, frame.manufacturer, _version(index), None)
        reading.issues.extend(index.issues)
        reading.meter_time = self._meter_time(index, reading)

        fields, issues, unknown = self.electricity.map(index)
        reading.electricity.update(fields)
        reading.issues.extend(issues)
        reading.unknown_codes.extend(unknown)

        ctx = DeriveContext(frame, index, received_at, reading.meter_time, reading.version, fields)
        reading.electricity.update(derive_all(ctx))
        reading.issues.extend(ctx.issues)

        channels = [MbusChannel.from_lines(n, lines) for n, lines in index.channels.items()]
        for channel in channels:
            reading.unknown_codes.extend(channel.unknown_codes)
        reading.mbus, issues = self.mbus.map(channels, received_at)
        reading.issues.extend(issues)
        return reading

    @staticmethod
    def _meter_time(index: LineIndex, reading: Reading) -> datetime | None:
        text = index.first_text("0-0:1.0.0")
        if text is None:
            return None
        moment = parse_meter_time(text)
        if moment is None:
            reading.issues.append(
                FieldIssue("field", "unreadable meter time", index.electricity["0-0:1.0.0"].raw)
            )
        return moment


def _version(index: LineIndex) -> str | None:
    nl = index.first_text("1-3:0.2.8")
    if nl:
        return nl
    be = index.first_text("0-0:96.1.4")
    return "BE" + be if be else None
