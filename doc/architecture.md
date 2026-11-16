# Architecture

- Status: **draft**, awaiting approval
- Date: 2026-11-16

## 1. Purpose

`emon2influx-parser` receives P1 telegrams that EMON kits publish over MQTT, parses them and
writes the readings to InfluxDB. It replaces the original `emon2influx` script.

The rewrite has these goals:

- **Missing is not zero.** A value that is not in the telegram is not written. Today a
  household without a gas meter gets `gas_delivered=0`.
- **Correct counters.** Import is always read from OBIS `1.8.x` and export from `2.8.x`.
  The old parser swapped them for telegrams without a version line (DSMR 2.2/3 and some
  others).
- **More fields.** Voltage, current, power per phase, voltage sags and swells, power failures,
  tariff, counters per tariff, and the extra fields of Belgian and Luxembourg meters.
  M-Bus meters for gas, water and heat are stored as well.
- **Nothing that identifies the household.** No EAN or equipment identifiers, no text
  messages from the grid operator, no meter serial numbers. The signature is the only key.
- **No lost data when InfluxDB is down.** Writes are buffered on disk and replayed later.
- **Robust.**
  - A bad message is rejected with a clear error; it never stops the service.
  - Partial data is written where it is safe.
- **Testable on the dev server.** A dry-run mode, replay of old logs, and a separate test
  bucket are available before production.

Out of scope:

- Changes to the kit firmware or the MQTT broker.
- Changes to the p1-meter-app. Consequences for the app are listed in §12.
- Rewriting historical data in InfluxDB.

## 2. Context

```
 EMON kit ──MQTT (QoS 0)──▶ broker 10.0.0.31 ──MQTT──▶ emon2influx-parser ──HTTP──▶ InfluxDB
 (P1 port)   topic smartmeter/raw                     (this project)               bucket p1-smartmeters
                                                                                   measurement dsmr
                                                                       read by ──▶ p1-meter-app
```

Observed load on 2026-11-16 (old logs, 17:10–18:49 UTC):

- 83 kits, all with prefix `2019-ETI-EMON-V01`.
- About 3 messages per second, one per kit every ~28 s.
- Telegrams by version: 57 kits DSMR 5.0, 23 kits DSMR 4.2, 3 kits without a version line.
- 1.7 % of the messages failed:
  - 158 corrupt JSON;
  - 127 non-telegram messages (`1`);
  - 4 truncated telegrams;
  - 3 invalid UTF-8.

## 3. Input

### 3.1 MQTT payload

UTF-8 JSON:

```json
{
  "datagram": {
    "p1": "/XMX5LGBB...\r\n\r\n1-3:0.2.8(50)\r\n...!7821\r\n",
    "signature": "2019-ETI-EMON-V01-A1B2C3-000000",
    "s0": { "unit": "W", "label": "e-car charger", "value": 0 },
    "s1": { "unit": "W", "label": "solar panels", "value": 0 }
  }
}
```

- This is the format of the V01 kits. V02 kits (`2026-MNEXT-EMON-V02`) will send a different
  payload, not yet known (§3.4).
- `s0` and `s1` (pulse inputs) are ignored.
- Messages that are not a JSON object with `datagram.p1` and `datagram.signature` are rejected. This covers the bare `1` and the "hello" message.

### 3.2 Signature

- It must match `^(<prefix>)-[0-9A-F]{6}-[0-9A-F]{6}$`, with `<prefix>` taken from
  `SIGNATURE_PREFIXES`.
- Anything else is rejected and never reaches InfluxDB. This prevents line-protocol injection and stray series.

### 3.3 Payload formats per kit generation

- **One decoder per payload format.** Each decoder turns its format into the same
  `Payload(signature, p1, received_at)`. The parser and everything after it do not depend on the kit generation.
- **A decoder registry maps signature prefixes to decoders.** It starts with `2019-ETI-EMON-V01` → `EmonV1Decoder`.
- **V02 is added once its format is known.** That means one new decoder class, its fixtures, and a registry entry.
- **Until then V02 messages are rejected** with category `payload_unsupported`. They are counted and quarantined, so the first real V02 messages are available as examples.

### 3.4 Telegram

- It starts at `/`, followed by the identification line, and ends at `!`, followed by an optional CRC16.
- **CRC:** with `REQUIRE_VALID_CRC=true` a telegram with a CRC that does not match is rejected. The CRC is CRC-16/ARC over `/` up to and including `!`.
- **Telegrams without a CRC** (DSMR 2.2/3) are accepted.
- **Version** comes from `1-3:0.2.8` (NL), `0-0:96.1.4` (BE) or is absent (old meters). It is stored as metadata only. Fields are recognised by their OBIS code, not by a parser per version, so a new version needs no new code path.

## 4. Output: InfluxDB schema

**Measurement:** `dsmr`. **Tag:** `signature` only, exactly as today. The five existing field
names keep their meaning, so the p1-meter-app keeps working. New fields are added. A field is
written only when its OBIS code is in the telegram and its value parses with the expected unit.

### 4.1 Electricity point

Timestamp: the time the message was received, in UTC (§5).

| Field                                                 | OBIS                              | Unit  | Notes                               |
| ----------------------------------------------------- | --------------------------------- | ----- | ----------------------------------- |
| `power_consumed`, `power_produced`                    | 1-0:1.7.0, 1-0:2.7.0              | kW    | existing                            |
| `energy_consumed`, `energy_produced`                  | sum of 1.8.1+1.8.2 / 2.8.1+2.8.2  | kWh   | existing; or 1.8.0 / 2.8.0 if given |
| `energy_consumed_t1`, `_t2`, `energy_produced_t1`, `_t2` | 1-0:1.8.1, 1.8.2, 2.8.1, 2.8.2 | kWh   | new                                 |
| `tariff`                                              | 0-0:96.14.0                       | –     | integer, 1 or 2                     |
| `power_consumed_l1`, `_l2`, `_l3`                     | 1-0:21.7.0, 41.7.0, 61.7.0        | kW    |                                     |
| `power_produced_l1`, `_l2`, `_l3`                     | 1-0:22.7.0, 42.7.0, 62.7.0        | kW    |                                     |
| `voltage_l1`, `_l2`, `_l3`                            | 1-0:32.7.0, 52.7.0, 72.7.0        | V     |                                     |
| `current_l1`, `_l2`, `_l3`                            | 1-0:31.7.0, 51.7.0, 71.7.0        | A     | often whole amperes                 |
| `voltage_sags_l1`, `_l2`, `_l3`                       | 1-0:32.32.0, 52.32.0, 72.32.0     | count | integer                             |
| `voltage_swells_l1`, `_l2`, `_l3`                     | 1-0:32.36.0, 52.36.0, 72.36.0     | count | integer                             |
| `power_failures`, `long_power_failures`               | 0-0:96.7.21, 0-0:96.7.9           | count | integer                             |
| `demand_current`                                      | 1-0:1.4.0                         | kW    | BE: current quarter-hour average    |
| `demand_max_month`                                    | 1-0:1.6.0                         | kW    | BE: monthly peak (value only)       |
| `power_threshold`                                     | 0-0:17.0.0                        | kW    | limiter threshold                   |
| `breaker_state`                                       | 0-0:96.3.10                       | –     | integer                             |
| `reactive_power_consumed`, `_produced`                | 1-0:3.7.0, 1-0:4.7.0              | kvar  | LU (Smarty)                         |
| `reactive_energy_consumed`, `_produced`               | 1-0:3.8.0, 1-0:4.8.0              | kvarh | LU (Smarty)                         |
| `meter_manufacturer`                                  | identification line               | –     | string: the 3-letter code (`KFM`, `XMX`, …) |
| `dsmr_version`                                        | 1-3:0.2.8 / 0-0:96.1.4            | –     | string, e.g. `50`, `42`, `BE50217`; absent when none |
| `clock_offset_s`                                      | 0-0:1.0.0                         | s     | receipt time minus meter clock      |

Units are normalised: `W` → kW and `Wh` → kWh. A value with an unexpected unit is skipped and
reported (§8).

The identification line is not stored as a whole. In some meters it contains the serial number
(`/XMX5LGBBFG10…`), which could identify the household. Only the manufacturer code is kept.

### 4.2 M-Bus points (gas, water, heat)

- Each M-Bus channel `0-n:` with a reading is written as its **own point**. The timestamp is the capture time from the telegram, converted to UTC.
- This puts the hourly gas readings of DSMR 4 meters on the hour they belong to, instead of up to an hour later.
- DSMR 5 meters produce one point per 5 minutes. Repeated messages with the same capture time overwrite the same point, so they do no harm.

| Device type `0-n:24.1.0` | Field             | Unit | OBIS                                                     |
| ------------------------ | ----------------- | ---- | -------------------------------------------------------- |
| 3 (gas)                  | `gas_delivered`   | m³   | 0-n:24.2.1 (NL), 0-n:24.2.3 (BE), 0-n:24.3.0 (DSMR 2.2/3) |
| 7 (water)                | `water_delivered` | m³   | 0-n:24.2.1                                               |
| 4 (heat)                 | `heat_delivered`  | GJ   | 0-n:24.2.1                                               |

When a telegram has no device type (old meters), a reading in m³ is taken as gas. Equipment
identifiers of M-Bus devices (`0-n:96.1.0`) are never read.

### 4.3 Tracing a field back to DSMR

Every field can be traced back to the OBIS code it came from.

- **One registry entry per field** (`telegram/registry.py`), with:
  - the OBIS code or codes;
  - the description from the DSMR specification;
  - the unit in the telegram and the stored unit;
  - the conversion (e.g. W → kW);
  - the meter generations or countries where it occurs.
- **Derived fields name their sources:**
  - `energy_consumed` = `1-0:1.8.1` + `1-0:1.8.2`, or `1-0:1.8.0`;
  - `clock_offset_s` = receipt time − `0-0:1.0.0`;
  - `meter_manufacturer` comes from the identification line.
- **Generated from the registry** by `python -m emon2influx.tools.fields`:
  - `doc/fields.md`, to read;
  - `doc/fields.json`, for scripts and Grafana.

  A test fails when either differs from the registry, so they cannot drift.
- **In InfluxDB:** a measurement `dsmr_fields` in the same bucket, with tag `field` and fields `obis`, `description`, `unit`, `derived_from`. It is written at start-up when the registry changed, so the mapping can also be found from Flux or Grafana. In dry-run mode it is not written.

### 4.4 Never stored

The following are never stored:

- `0-0:96.1.1` and `0-n:96.1.0`: equipment identifiers (EAN or serial).
- `0-0:96.13.0` and `0-0:96.13.1`: text and code messages.
- `1-0:99.97.0`: the power failure event log with timestamps. The counters are stored instead.
- The full identification line (see §4.1).
- The raw telegram. It is only written to the quarantine file of a failed parse, with identifiers removed (§8).

## 5. Time

- InfluxDB stores everything in UTC. All timestamps are converted to UTC before writing.
- **Electricity:** the receipt time. The meter clock was measured against the receipt time on 2026-11-16:
  - the median difference was 3 s;
  - the 5–95 % range was −45 s to +48 s;
  - the extremes were −58 s and +72 s;
  - the offset is constant per meter (clock drift).

  The receipt time is therefore more accurate. The offset is stored as `clock_offset_s`.
- **M-Bus (gas, water, heat):** the capture time from the telegram. It is converted from
  Dutch local time:
  - a suffix `S` means summer time (UTC+2), `W` winter time (UTC+1);
  - without a suffix (old meters), the time is converted with the `Europe/Amsterdam` zone.

  A capture time more than 2 hours away from the receipt time is not trusted: the reading is then written at the receipt time and reported.

## 6. Components

Python 3.12 package `emon2influx`. Dependencies: `paho-mqtt` 2.x, `influxdb-client`,
`python-dotenv`. All are permissively licensed.

```
 MqttSource ──bytes──▶ queue ──▶ Pipeline ──▶ PayloadDecoder ──▶ TelegramParser ──▶ PointBuilder ──▶ InfluxSink
 (paho thread)        (bounded)  (worker)        │                    │                                 │
                                                 └──── errors ───────▶ ErrorReporter ◀── write errors ──┤
                                                                       (error log, quarantine,          ▼
                                                                        hourly summary)              DiskSpool
```

| Module                     | Class / role                                                                 |
| -------------------------- | ---------------------------------------------------------------------------- |
| `config.py`                | `Settings`: reads and validates the environment (`.env`); fails fast with a clear message |
| `source/mqtt.py`           | `MqttSource`: paho 2.x client, subscribes in `on_connect`, automatic reconnect, puts raw payloads with their receipt time on a bounded queue |
| `payload/`                 | `PayloadDecoder` interface → `Payload(signature, p1, received_at)`; `EmonV1Decoder`; `DecoderRegistry` (prefix → decoder, §3.3) |
| `signature.py`             | `Signature`: validation against the allowed prefixes; `masked()` for logs     |
| `telegram/frame.py`        | `TelegramFrame`: splits header, lines and CRC; `crc16()`                     |
| `telegram/obis.py`         | `ObisLine` (code, values, units) and the lexer for all line forms, including the multi-line DSMR 2.2 gas reading |
| `telegram/registry.py`     | `FieldSpec` and the `REGISTRY`: OBIS code → field, unit, type, conversion and description. Adding a field is one entry. The single source for §4.3 |
| `telegram/parser.py`       | `TelegramParser` → `Reading(electricity: dict, mbus: list[MbusReading], meter: MeterInfo, meter_time)`; collects `FieldIssue`s instead of failing on one bad line |
| `telegram/mbus.py`         | `MbusChannel`: groups `0-n:` lines per channel and maps device types         |
| `points.py`                | `PointBuilder`: `Reading` → InfluxDB points (escaping by the client library) |
| `sink/influx.py`           | `InfluxSink`: batching, retry with backoff, hands failed batches to the spool |
| `sink/spool.py`            | `DiskSpool`: write-ahead files in line protocol, replay oldest first, size cap |
| `diagnostics.py`           | `ErrorReporter`: error log, quarantine file, rate limiting, hourly summary, counters |
| `app.py`, `__main__.py`    | wiring, `MODE` (write or dry-run), signal handling (SIGTERM, SIGINT), clean shutdown with a final flush |
| `tools/replay.py`          | replays an old `emon2influx.log` through the new parser and compares with the old InfluxDB lines (§10) |

**Threads.**
- **paho network thread:** only puts payloads on the queue, so it never blocks.
- **Worker thread:** parses, builds points and writes them.
- **Sink flush thread:** writes batches and empties the spool.
- **Queue limit:** the queue holds 10,000 messages. When it is full, the oldest are dropped and counted. That only happens if parsing cannot keep up.

## 7. Buffering when InfluxDB is unavailable

1. **Batching.** Points are written in batches of `INFLUX_BATCH_SIZE` or after `INFLUX_FLUSH_INTERVAL_MS`.
2. **Spooling.** A batch that fails with a timeout, a connection error or HTTP 5xx/429 is appended to a file in `SPOOL_DIR`, in line protocol, one file per batch. Then `fsync`.
3. **Replay.**
   - While the spool is not empty, new batches go to the spool too, so the order is kept.
   - A replay loop sends the oldest file first, with a backoff of 1 s up to 5 min.
   - A file is deleted only after InfluxDB accepted it.
4. **Replay is idempotent.** InfluxDB overwrites a point with the same series and timestamp, so a crash during replay does not create duplicates.
5. **Restarts.** After a restart the existing spool is replayed first.
6. **Size cap.** The spool is capped at `SPOOL_MAX_MB`, default 2 GB. That is roughly 10 days at the current load. When it is full, the oldest files are dropped and the number of lost points is logged as an error.
7. **4xx responses are not retried.** A 4xx other than 429 means bad data or missing permission. The batch goes to the quarantine file and is reported.

Limit: messages that never arrive are lost. That happens when the parser itself or the MQTT connection is down, because the kits publish with QoS 0 and the broker does not keep them.

## 8. Errors and diagnostics

| Category          | Example                              | Effect                         |
| ----------------- | ------------------------------------ | ------------------------------ |
| `payload_json`    | corrupt bytes, invalid UTF-8         | message rejected               |
| `payload_shape`   | `1`, missing `datagram.p1`           | message rejected (logged at DEBUG; expected noise) |
| `payload_unsupported` | prefix without a decoder (V02 for now) | message rejected and quarantined |
| `signature`       | wrong prefix or format               | message rejected               |
| `crc`             | CRC mismatch                         | message rejected               |
| `telegram`        | no `/` or `!`, truncated             | message rejected               |
| `field`           | unparsable value, unexpected unit    | field skipped, rest written    |
| `mbus_time`       | capture time far from receipt time   | reading written at receipt time |
| `influx_rejected` | 4xx from InfluxDB                    | batch quarantined              |
| `spool_dropped`   | spool full                           | oldest points dropped          |

**Error log** (`logs/errors.log`, rotated hourly). One record per error:
- time;
- category;
- signature;
- meter manufacturer and DSMR version;
- the offending telegram line;
- a short message.

**Quarantine** (`QUARANTINE_FILE`, JSON Lines). For each rejected message: time, category, and the raw payload with identifier values (`96.1.1`, `96.1.0`, `96.13.x`) replaced by `…`. It can be replayed later and turned into test cases (with fictitious signatures).

**Rate limiting.** Each combination of kit and category is logged in full `ERROR_LOG_LIMIT_PER_HOUR` times per hour, then counted only.

**Hourly summary** in the main log:
- messages, points written;
- errors per category;
- the spool size;
- kits seen.

The main log no longer contains every message at INFO.

## 9. Configuration

- **Everything comes from the environment.**
  - `.env` is loaded with `python-dotenv` in development; production uses systemd `EnvironmentFile`.
  - `.env` is git-ignored. `.env.example` lists every key.
- **Validation at start.** Missing or invalid settings stop the service with a clear message.
- **`MODE` defaults to `dry-run`.** Points are then built, counted and logged at DEBUG, but not written.

## 10. Testing

**Unit tests (pytest):**
- the frame and the CRC;
- the OBIS lexer, including the multi-line DSMR 2.2 gas reading;
- every registry entry;
- M-Bus channels;
- time conversion around the summer/winter switch;
- signature validation and injection attempts;
- the point builder;
- the spool, including a restart, the size cap and replay order;
- the sink, including retry and 4xx handling, with a fake HTTP client.

All test telegrams use fictitious identifiers and signatures, such as `A1B2C3`.

**Fixtures:** synthetic telegrams for:
- DSMR 2.2, 4.2 and 5.0;
- Belgian ESMR 5 and Luxembourg Smarty;
- a meter without gas;
- a water meter;
- broken telegrams.

**Quality:** `ruff` (lint and format) and `mypy --strict`.

**On the dev server, before production:**
1. **Replay.** `python -m emon2influx.tools.replay <old log>` runs the old logs through the new parser. Per kit it reports differences from the old InfluxDB lines. The expected differences are:
   - the counters of meters without a version line;
   - gas no longer written when absent;
   - the new fields.

   Anything else is a bug.
2. **Live dry-run** against the broker, with the read-only MQTT user and its own client id, for at least a day.
3. **Write mode to the test bucket** `p1-smartmeters-test` with a token for that bucket only. The test includes stopping InfluxDB (or blocking the URL) to exercise the spool.

Only after these steps is switching production discussed.

## 11. Deployment

- **systemd service** `emon2influx-parser.service`:
  - runs `python -m emon2influx` in a venv;
  - `EnvironmentFile=/etc/emon2influx-parser/env` (mode 600);
  - `StateDirectory` for the spool, `LogsDirectory` for logs;
  - `Restart=always`;
  - hardening options.
- **Docker:** a `Dockerfile` as an alternative.
- **Switching over.**
  - Old and new parser can run side by side, each with its own client id.
  - Only one may write to `p1-smartmeters`.
  - The switch itself is a separate decision.

## 12. Consequences for the p1-meter-app

These are not part of this project; they are listed here so they can be planned:

- **Swapped counters.** Detection must stay, because history keeps the swapped data. After the switch, new data from the affected kits is correct. A household may then flip orientation at the switch date.
- **Missing gas.** Households without gas get no `gas_delivered` at all instead of 0. The rule "gas only if `max > 0`" keeps working.
- **Gas at its capture time.** The app shifts hourly gas back by one hour (gas analysis §3.6). For data written by the new parser that shift must not be applied, or it will be double.
- **New fields** (voltage, current, per phase) can be used later.

## 13. Open questions

| #   | Topic                       | Proposal                                                                   |
| --- | --------------------------- | -------------------------------------------------------------------------- |
| 1   | V02 kits payload            | Different from V01, not yet known. A decoder is added when it is (§3.3). |
| 5   | Field map in InfluxDB       | Yes: `dsmr_fields` measurement next to the docs (§4.3). Decided 2026-11-16. |
| 2   | Approximated cos φ          | Not stored (not in DSMR; P/(U×I) is inaccurate with whole amperes).        |
| 3   | GitHub repository           | Local only for now; AvansETI later after approval.                         |
| 4   | InfluxDB host               | Dev server: `https://influxdb.sendlab.nl`; production: the local IP address. Decided 2026-11-16. |
