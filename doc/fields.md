# Fields

Every field in the `dsmr` measurement and the DSMR/OBIS code it comes from.
Generated from `src/emon2influx/telegram/registry.py` by
`python -m emon2influx.tools.fields`; do not edit by hand.
Registry version `4d833267a5c9453b`; the same map is in the `dsmr_fields`
measurement in InfluxDB.

A field is only written when its code is in the telegram. M-Bus fields
(`point: mbus`) are separate points at the capture time from the telegram; the
others share one point at the receipt time.

| Field | OBIS | Unit | Type | Point | Occurs | Description |
| ----- | ---- | ---- | ---- | ----- | ------ | ----------- |
| `power_consumed` | `1-0:1.7.0` | kW | float | electricity | all | Actual electricity power delivered to the client (+P) |
| `power_produced` | `1-0:2.7.0` | kW | float | electricity | all | Actual electricity power received from the client (-P) |
| `energy_consumed_t1` | `1-0:1.8.1` | kWh | float | electricity | all | Meter reading, electricity delivered to the client, tariff 1 |
| `energy_consumed_t2` | `1-0:1.8.2` | kWh | float | electricity | all | Meter reading, electricity delivered to the client, tariff 2 |
| `energy_produced_t1` | `1-0:2.8.1` | kWh | float | electricity | all | Meter reading, electricity delivered by the client, tariff 1 |
| `energy_produced_t2` | `1-0:2.8.2` | kWh | float | electricity | all | Meter reading, electricity delivered by the client, tariff 2 |
| `tariff` | `0-0:96.14.0` | - | int | electricity | all | Tariff indicator electricity |
| `power_consumed_l1` | `1-0:21.7.0` | kW | float | electricity | NL DSMR 4/5 | Instantaneous active power delivered to the client (+P), phase L1 |
| `power_consumed_l2` | `1-0:41.7.0` | kW | float | electricity | NL DSMR 4/5 | Instantaneous active power delivered to the client (+P), phase L2 |
| `power_consumed_l3` | `1-0:61.7.0` | kW | float | electricity | NL DSMR 4/5 | Instantaneous active power delivered to the client (+P), phase L3 |
| `power_produced_l1` | `1-0:22.7.0` | kW | float | electricity | NL DSMR 4/5 | Instantaneous active power received from the client (-P), phase L1 |
| `power_produced_l2` | `1-0:42.7.0` | kW | float | electricity | NL DSMR 4/5 | Instantaneous active power received from the client (-P), phase L2 |
| `power_produced_l3` | `1-0:62.7.0` | kW | float | electricity | NL DSMR 4/5 | Instantaneous active power received from the client (-P), phase L3 |
| `voltage_l1` | `1-0:32.7.0` | V | float | electricity | NL DSMR 4/5 | Instantaneous voltage, phase L1 |
| `voltage_l2` | `1-0:52.7.0` | V | float | electricity | NL DSMR 4/5 | Instantaneous voltage, phase L2 |
| `voltage_l3` | `1-0:72.7.0` | V | float | electricity | NL DSMR 4/5 | Instantaneous voltage, phase L3 |
| `current_l1` | `1-0:31.7.0` | A | float | electricity | NL DSMR 4/5 | Instantaneous current, phase L1 |
| `current_l2` | `1-0:51.7.0` | A | float | electricity | NL DSMR 4/5 | Instantaneous current, phase L2 |
| `current_l3` | `1-0:71.7.0` | A | float | electricity | NL DSMR 4/5 | Instantaneous current, phase L3 |
| `voltage_sags_l1` | `1-0:32.32.0` | - | int | electricity | NL DSMR 4/5 | Number of voltage sags, phase L1 |
| `voltage_sags_l2` | `1-0:52.32.0` | - | int | electricity | NL DSMR 4/5 | Number of voltage sags, phase L2 |
| `voltage_sags_l3` | `1-0:72.32.0` | - | int | electricity | NL DSMR 4/5 | Number of voltage sags, phase L3 |
| `voltage_swells_l1` | `1-0:32.36.0` | - | int | electricity | NL DSMR 4/5 | Number of voltage swells, phase L1 |
| `voltage_swells_l2` | `1-0:52.36.0` | - | int | electricity | NL DSMR 4/5 | Number of voltage swells, phase L2 |
| `voltage_swells_l3` | `1-0:72.36.0` | - | int | electricity | NL DSMR 4/5 | Number of voltage swells, phase L3 |
| `power_failures` | `0-0:96.7.21` | - | int | electricity | NL DSMR 4/5 | Number of power failures in any phase |
| `long_power_failures` | `0-0:96.7.9` | - | int | electricity | NL DSMR 4/5 | Number of long power failures in any phase |
| `demand_current` | `1-0:1.4.0` | kW | float | electricity | BE e-MUCS | Current average demand, active energy import (quarter hour) |
| `demand_max_month` | `1-0:1.6.0` | kW | float | electricity | BE e-MUCS | Maximum demand of the running month, active energy import |
| `power_threshold` | `0-0:17.0.0` | kW | float | electricity | DSMR 2.2/3, BE | Actual threshold electricity (limiter) |
| `breaker_state` | `0-0:96.3.10` | - | int | electricity | DSMR 2.2/3, BE | Breaker state (0 disconnected, 1 connected, 2 ready for reconnection) |
| `current_threshold` | `1-0:31.4.0` | A | float | electricity | BE e-MUCS | Limiter current threshold |
| `reactive_power_consumed` | `1-0:3.7.0` | kvar | float | electricity | LU Smarty | Instantaneous reactive power import (+Q) |
| `reactive_power_produced` | `1-0:4.7.0` | kvar | float | electricity | LU Smarty | Instantaneous reactive power export (-Q) |
| `reactive_energy_consumed` | `1-0:3.8.0` | kvarh | float | electricity | LU Smarty | Meter reading, reactive energy import (+Q) |
| `reactive_energy_produced` | `1-0:4.8.0` | kvarh | float | electricity | LU Smarty | Meter reading, reactive energy export (-Q) |
| `energy_consumed` | `1-0:1.8.1`, `1-0:1.8.2`, `1-0:1.8.0` (derived) | kWh | float | electricity | all | Electricity delivered to the client, all tariffs: 1.8.1 + 1.8.2, or 1.8.0 when the meter has no tariff registers |
| `energy_produced` | `1-0:2.8.1`, `1-0:2.8.2`, `1-0:2.8.0` (derived) | kWh | float | electricity | all | Electricity delivered by the client, all tariffs: 2.8.1 + 2.8.2, or 2.8.0 |
| `clock_offset_s` | `0-0:1.0.0` (derived) | s | int | electricity | all | Receipt time minus the meter clock, in whole seconds |
| `meter_manufacturer` | `identification line` (derived) | - | string | electricity | all | 3-letter manufacturer code from the identification line (e.g. KFM, XMX); the rest of that line is not stored because it may contain a serial number |
| `crc_valid` | `CRC after !` (derived) | - | bool | electricity | all | Whether the CRC16 of the telegram matched; absent for meters without a CRC. Telegrams with a wrong CRC are still written (their values were found plausible), so they can be filtered or analysed later |
| `dsmr_version` | `1-3:0.2.8`, `0-0:96.1.4` (derived) | - | string | electricity | all | P1 version, e.g. 50 or 42; BE versions are prefixed with BE |
| `gas_delivered` | `0-n:24.2.1`, `0-n:24.2.3`, `0-n:24.3.0` | m3 | float | mbus | M-Bus device type 3 | Gas meter reading; time = capture time from the telegram (0-n:24.3.0: DSMR 2.2/3, 0-n:24.2.3: BE) |
| `water_delivered` | `0-n:24.2.1` | m3 | float | mbus | M-Bus device type 7 | Water meter reading; time = capture time from the telegram |
| `heat_delivered` | `0-n:24.2.1` | GJ | float | mbus | M-Bus device type 4 | Heat meter reading; time = capture time from the telegram |

## Read but not stored under their own name

| OBIS | Meaning |
| ---- | ------- |
| `1-0:1.8.0` | Meter reading, electricity delivered to the client, total |
| `1-0:2.8.0` | Meter reading, electricity delivered by the client, total |
| `0-0:1.0.0` | Date and time of the telegram (meter clock) |
| `1-3:0.2.8` | Version information for P1 output (NL) |
| `0-0:96.1.4` | Version information (BE) |
| `0-n:24.1.0` | M-Bus device type |

## Never stored

These would lead to the household or carry free text.

| OBIS | Meaning |
| ---- | ------- |
| `0-0:96.1.1` | Equipment identifier (EAN/serial) of the electricity meter |
| `0-n:96.1.0` | Equipment identifier of an M-Bus device |
| `0-0:96.13.0` | Text message from the grid operator |
| `0-0:96.13.1` | Text message codes |
| `1-0:99.97.0` | Power failure event log (the counters are stored instead) |
| `0-n:24.4.0` | Gas valve position (DSMR 2.2/3); a switch state, not a measurement |
