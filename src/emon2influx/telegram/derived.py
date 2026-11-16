"""Derived fields: one function per `DerivedSpec` in the registry (doc/architecture.md §4.1).

Each function gets the telegram's context and returns the value, or None when the telegram does
not have what it needs (then the field is not written). A test checks that every derived field
in the registry has exactly one function here.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .electricity import convert
from .frame import TelegramFrame
from .lines import LineIndex
from .model import FieldIssue, Value
from .registry import BY_OBIS, FieldSpec


@dataclass
class DeriveContext:
    frame: TelegramFrame
    index: LineIndex
    received_at: datetime
    meter_time: datetime | None
    version: str | None
    fields: dict[str, Value]
    issues: list[FieldIssue] = field(default_factory=list)


Deriver = Callable[[DeriveContext], Value | None]


def _total(direction: str, total_code: str) -> Deriver:
    """Sum of the two tariff registers, or the total register of meters without tariffs."""

    def derive(ctx: DeriveContext) -> Value | None:
        t1 = ctx.fields.get(f"energy_{direction}_t1")
        t2 = ctx.fields.get(f"energy_{direction}_t2")
        if isinstance(t1, float) and isinstance(t2, float):
            return round(t1 + t2, 6)
        line = ctx.index.electricity.get(total_code)
        if line is None:
            return None
        spec = FieldSpec(
            total_code, f"energy_{direction}", "kWh", "float", "", "", BY_OBIS["1-0:1.8.1"].units
        )
        result = convert(spec, line)
        if isinstance(result, FieldIssue):
            ctx.issues.append(result)
            return None
        return result

    return derive


def _clock_offset(ctx: DeriveContext) -> Value | None:
    if ctx.meter_time is None:
        return None
    return round((ctx.received_at - ctx.meter_time).total_seconds())


def _crc_valid(ctx: DeriveContext) -> Value | None:
    frame = ctx.frame
    if frame.crc_valid is False:
        assert frame.crc is not None and frame.crc_computed is not None
        ctx.issues.append(
            FieldIssue(
                "crc",
                f"CRC mismatch, written with crc_valid=false: telegram says {frame.crc:04X}, "
                f"computed {frame.crc_computed:04X}",
            )
        )
    return frame.crc_valid


DERIVERS: dict[str, Deriver] = {
    "energy_consumed": _total("consumed", "1-0:1.8.0"),
    "energy_produced": _total("produced", "1-0:2.8.0"),
    "clock_offset_s": _clock_offset,
    "crc_valid": _crc_valid,
    "meter_manufacturer": lambda ctx: ctx.frame.manufacturer,
    "dsmr_version": lambda ctx: ctx.version,
}


def derive_all(ctx: DeriveContext) -> dict[str, Value]:
    values: dict[str, Value] = {}
    for name, deriver in DERIVERS.items():
        value = deriver(ctx)
        if value is not None:
            values[name] = value
    return values
