"""The result of parsing one telegram."""

from dataclasses import dataclass, field
from datetime import datetime

Value = float | int | str | bool


@dataclass(frozen=True)
class FieldIssue:
    """A problem with one line; the rest of the telegram is still used."""

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
