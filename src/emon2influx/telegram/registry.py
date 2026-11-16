"""Every stored field and the DSMR/OBIS code it comes from (doc/architecture.md §4).

This module is the single source for the parser, `doc/fields.md`, `doc/fields.json` and the
`dsmr_fields` measurement. Adding a field is one entry here.
"""

import hashlib
import json
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Literal

Kind = Literal["float", "int", "string", "bool"]

KW = {"kW": 1.0, "W": 0.001}
KWH = {"kWh": 1.0, "Wh": 0.001}
KVAR = {"kvar": 1.0, "var": 0.001}
KVARH = {"kvarh": 1.0, "varh": 0.001}
VOLT = {"V": 1.0}
AMPERE = {"A": 1.0}
M3 = {"m3": 1.0}
GJ = {"GJ": 1.0, "MJ": 0.001, "MWh": 3.6, "kWh": 0.0036}

NL = "NL DSMR 4/5"
BE = "BE e-MUCS"
LU = "LU Smarty"


@dataclass(frozen=True)
class FieldSpec:
    """A field read directly from one electricity line; the value is the last group."""

    obis: str
    field: str
    unit: str | None
    kind: Kind
    description: str
    occurs: str
    units: dict[str, float] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class DerivedSpec:
    """A field computed from other lines or from the identification line."""

    field: str
    sources: tuple[str, ...]
    unit: str | None
    kind: Kind
    description: str


@dataclass(frozen=True)
class MbusSpec:
    """A meter on an M-Bus channel `0-n:`, by its device type `0-n:24.1.0`."""

    device_type: int
    field: str
    unit: str
    obis: tuple[str, ...]
    description: str
    units: dict[str, float]


def _phases(
    obis: str,
    name: str,
    unit: str | None,
    kind: Kind,
    text: str,
    units: dict[str, float],
    occurs: str = NL,
) -> list[FieldSpec]:
    """Three entries for L1, L2, L3; OBIS C is 20 higher per phase (e.g. 32 → 52 → 72)."""
    medium, rest = obis.split(":")
    c, d, e = rest.split(".")
    return [
        FieldSpec(
            f"{medium}:{int(c) + 20 * i}.{d}.{e}",
            f"{name}_l{i + 1}",
            unit,
            kind,
            f"{text}, phase L{i + 1}",
            occurs,
            units,
        )
        for i in range(3)
    ]


ELECTRICITY: tuple[FieldSpec, ...] = (
    FieldSpec(
        "1-0:1.7.0",
        "power_consumed",
        "kW",
        "float",
        "Actual electricity power delivered to the client (+P)",
        "all",
        KW,
    ),
    FieldSpec(
        "1-0:2.7.0",
        "power_produced",
        "kW",
        "float",
        "Actual electricity power received from the client (-P)",
        "all",
        KW,
    ),
    FieldSpec(
        "1-0:1.8.1",
        "energy_consumed_t1",
        "kWh",
        "float",
        "Meter reading, electricity delivered to the client, tariff 1",
        "all",
        KWH,
    ),
    FieldSpec(
        "1-0:1.8.2",
        "energy_consumed_t2",
        "kWh",
        "float",
        "Meter reading, electricity delivered to the client, tariff 2",
        "all",
        KWH,
    ),
    FieldSpec(
        "1-0:2.8.1",
        "energy_produced_t1",
        "kWh",
        "float",
        "Meter reading, electricity delivered by the client, tariff 1",
        "all",
        KWH,
    ),
    FieldSpec(
        "1-0:2.8.2",
        "energy_produced_t2",
        "kWh",
        "float",
        "Meter reading, electricity delivered by the client, tariff 2",
        "all",
        KWH,
    ),
    FieldSpec("0-0:96.14.0", "tariff", None, "int", "Tariff indicator electricity", "all"),
    *_phases(
        "1-0:21.7.0",
        "power_consumed",
        "kW",
        "float",
        "Instantaneous active power delivered to the client (+P)",
        KW,
    ),
    *_phases(
        "1-0:22.7.0",
        "power_produced",
        "kW",
        "float",
        "Instantaneous active power received from the client (-P)",
        KW,
    ),
    *_phases("1-0:32.7.0", "voltage", "V", "float", "Instantaneous voltage", VOLT),
    *_phases("1-0:31.7.0", "current", "A", "float", "Instantaneous current", AMPERE),
    *_phases("1-0:32.32.0", "voltage_sags", None, "int", "Number of voltage sags", {}),
    *_phases("1-0:32.36.0", "voltage_swells", None, "int", "Number of voltage swells", {}),
    FieldSpec(
        "0-0:96.7.21", "power_failures", None, "int", "Number of power failures in any phase", NL
    ),
    FieldSpec(
        "0-0:96.7.9",
        "long_power_failures",
        None,
        "int",
        "Number of long power failures in any phase",
        NL,
    ),
    FieldSpec(
        "1-0:1.4.0",
        "demand_current",
        "kW",
        "float",
        "Current average demand, active energy import (quarter hour)",
        BE,
        KW,
    ),
    FieldSpec(
        "1-0:1.6.0",
        "demand_max_month",
        "kW",
        "float",
        "Maximum demand of the running month, active energy import",
        BE,
        KW,
    ),
    FieldSpec(
        "0-0:17.0.0",
        "power_threshold",
        "kW",
        "float",
        "Actual threshold electricity (limiter)",
        "DSMR 2.2/3, BE",
        KW,
    ),
    FieldSpec(
        "0-0:96.3.10",
        "breaker_state",
        None,
        "int",
        "Breaker state (0 disconnected, 1 connected, 2 ready for reconnection)",
        "DSMR 2.2/3, BE",
    ),
    FieldSpec(
        "1-0:31.4.0", "current_threshold", "A", "float", "Limiter current threshold", BE, AMPERE
    ),
    FieldSpec(
        "1-0:3.7.0",
        "reactive_power_consumed",
        "kvar",
        "float",
        "Instantaneous reactive power import (+Q)",
        LU,
        KVAR,
    ),
    FieldSpec(
        "1-0:4.7.0",
        "reactive_power_produced",
        "kvar",
        "float",
        "Instantaneous reactive power export (-Q)",
        LU,
        KVAR,
    ),
    FieldSpec(
        "1-0:3.8.0",
        "reactive_energy_consumed",
        "kvarh",
        "float",
        "Meter reading, reactive energy import (+Q)",
        LU,
        KVARH,
    ),
    FieldSpec(
        "1-0:4.8.0",
        "reactive_energy_produced",
        "kvarh",
        "float",
        "Meter reading, reactive energy export (-Q)",
        LU,
        KVARH,
    ),
)

# Read by the parser for derived fields only; never stored under their own name.
SOURCE_ONLY: dict[str, str] = {
    "1-0:1.8.0": "Meter reading, electricity delivered to the client, total",
    "1-0:2.8.0": "Meter reading, electricity delivered by the client, total",
    "0-0:1.0.0": "Date and time of the telegram (meter clock)",
    "1-3:0.2.8": "Version information for P1 output (NL)",
    "0-0:96.1.4": "Version information (BE)",
    "0-n:24.1.0": "M-Bus device type",
}

DERIVED: tuple[DerivedSpec, ...] = (
    DerivedSpec(
        "energy_consumed",
        ("1-0:1.8.1", "1-0:1.8.2", "1-0:1.8.0"),
        "kWh",
        "float",
        "Electricity delivered to the client, all tariffs: 1.8.1 + 1.8.2, or 1.8.0 "
        "when the meter has no tariff registers",
    ),
    DerivedSpec(
        "energy_produced",
        ("1-0:2.8.1", "1-0:2.8.2", "1-0:2.8.0"),
        "kWh",
        "float",
        "Electricity delivered by the client, all tariffs: 2.8.1 + 2.8.2, or 2.8.0",
    ),
    DerivedSpec(
        "clock_offset_s",
        ("0-0:1.0.0",),
        "s",
        "int",
        "Receipt time minus the meter clock, in whole seconds",
    ),
    DerivedSpec(
        "meter_manufacturer",
        ("identification line",),
        None,
        "string",
        "3-letter manufacturer code from the identification line (e.g. KFM, XMX); the "
        "rest of that line is not stored because it may contain a serial number",
    ),
    DerivedSpec(
        "crc_valid",
        ("CRC after !",),
        None,
        "bool",
        "Whether the CRC16 of the telegram matched; absent for meters without a CRC. "
        "Telegrams with a wrong CRC are still written (their values were found plausible), "
        "so they can be filtered or analysed later",
    ),
    DerivedSpec(
        "dsmr_version",
        ("1-3:0.2.8", "0-0:96.1.4"),
        None,
        "string",
        "P1 version, e.g. 50 or 42; BE versions are prefixed with BE",
    ),
)

MBUS: tuple[MbusSpec, ...] = (
    MbusSpec(
        3,
        "gas_delivered",
        "m3",
        ("0-n:24.2.1", "0-n:24.2.3", "0-n:24.3.0"),
        "Gas meter reading; time = capture time from the telegram (0-n:24.3.0: DSMR 2.2/3, "
        "0-n:24.2.3: BE)",
        M3,
    ),
    MbusSpec(
        7,
        "water_delivered",
        "m3",
        ("0-n:24.2.1",),
        "Water meter reading; time = capture time from the telegram",
        M3,
    ),
    MbusSpec(
        4,
        "heat_delivered",
        "GJ",
        ("0-n:24.2.1",),
        "Heat meter reading; time = capture time from the telegram",
        GJ,
    ),
)

# Codes that are deliberately never stored (doc/architecture.md §4.4).
EXCLUDED: dict[str, str] = {
    "0-0:96.1.1": "Equipment identifier (EAN/serial) of the electricity meter",
    "0-n:96.1.0": "Equipment identifier of an M-Bus device",
    "0-0:96.13.0": "Text message from the grid operator",
    "0-0:96.13.1": "Text message codes",
    "1-0:99.97.0": "Power failure event log (the counters are stored instead)",
    "0-n:24.4.0": "Gas valve position (DSMR 2.2/3); a switch state, not a measurement",
}

BY_OBIS: dict[str, FieldSpec] = {spec.obis: spec for spec in ELECTRICITY}
MBUS_BY_TYPE: dict[int, MbusSpec] = {spec.device_type: spec for spec in MBUS}
MBUS_READINGS = frozenset(code for spec in MBUS for code in spec.obis)

_names = [s.field for s in ELECTRICITY] + [d.field for d in DERIVED] + [m.field for m in MBUS]
assert len(_names) == len(set(_names)), "duplicate field name in the registry"


def field_map() -> list[dict[str, object]]:
    """All fields with their origin, as written to doc/fields.json and `dsmr_fields`."""
    rows: list[dict[str, object]] = []
    for spec in ELECTRICITY:
        rows.append(
            {
                "field": spec.field,
                "obis": [spec.obis],
                "unit": spec.unit,
                "type": spec.kind,
                "description": spec.description,
                "occurs": spec.occurs,
                "point": "electricity",
                "telegram_units": sorted(spec.units),
            }
        )
    for derived in DERIVED:
        rows.append(
            {
                "field": derived.field,
                "obis": list(derived.sources),
                "unit": derived.unit,
                "type": derived.kind,
                "description": derived.description,
                "occurs": "all",
                "point": "electricity",
                "derived": True,
            }
        )
    for mbus in MBUS:
        rows.append(
            {
                "field": mbus.field,
                "obis": list(mbus.obis),
                "unit": mbus.unit,
                "type": "float",
                "description": mbus.description,
                "occurs": f"M-Bus device type {mbus.device_type}",
                "point": "mbus",
                "telegram_units": sorted(mbus.units),
            }
        )
    return rows


def registry_hash() -> str:
    """Changes whenever any field definition changes; decides when `dsmr_fields` is rewritten."""
    payload = json.dumps(
        {"map": field_map(), "excluded": EXCLUDED, "source_only": SOURCE_ONLY}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
