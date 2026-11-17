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
- Rewriting historical data in InfluxDB, except the one-off counter correction (§13).

## 2. Context

```
 EMON kit ──MQTT (QoS 0)──▶ broker (internal) ──MQTT─▶ emon2influx-parser ──HTTP──▶ InfluxDB
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
- **CRC:** CRC-16/ARC over `/` up to and including `!`. The result is stored as `crc_valid`.
  - The old logs of 2026-11-16 showed a wrong CRC in 7.7 % of the telegrams that have one: never for half of the kits, always for two. In 648 checked cases the counters lay between those of the previous and next good telegram of the same kit, so the values are plausible. The cause, probably in the kit firmware, is not known.
  - Such telegrams are therefore **written with `crc_valid=false`**, counted, and logged as a warning (rate-limited).
  - With `REQUIRE_VALID_CRC=true` they are rejected instead.
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
| `crc_valid`                                           | CRC after `!`                     | –     | boolean; absent without a CRC (§3.4) |
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

Each class has one responsibility; collaborators are passed in through the constructor
(dependency injection), and only `container.py` knows which concrete classes are used. The
class diagrams are in [class-diagram.md](class-diagram.md).

```
 MqttSource ──bytes──▶ queue ──▶ Worker ──▶ Pipeline ──▶ DecoderRegistry ──▶ TelegramParser ──▶ PointBuilder
 (paho thread)        (bounded)  (worker      │                                                     │
                                  thread)     ▼                                                     ▼
                               ErrorReporter ──▶ ErrorLog, QuarantineStore              InfluxSink ──▶ Delivery ──▶ Writer
                                     │            (RateLimiter)                         (sink thread)   BackoffPolicy
                                     ▼                                                                  SpoolReplayer ──▶ DiskSpool
                                  Metrics ◀──────────────── every component counts here ──────────────────────┘
                                     │
                                     ▼
                              SummaryReporter (hourly line in the main log)
```

| Module                    | Class: responsibility |
| ------------------------- | --------------------- |
| `config.py`               | `Settings`: reads and validates the environment; fails fast with every problem listed |
| `container.py`            | `build()`: creates and connects all components (factory); the only place that picks concrete classes |
| `app.py`, `__main__.py`   | `App`: lifecycle order (start, run, stop) and signals (SIGTERM, SIGINT) |
| `worker.py`               | `Worker`: the processing loop; takes messages off the queue and hands points to the sink |
| `logging_setup.py`        | `setup_logging()`: where log records go (§8) |
| `metrics.py`              | `Metrics`: all counters, thread-safe; `snapshot(reset)` |
| `summary.py`              | `SummaryReporter`: the hourly summary line |
| `source/mqtt.py`          | `MqttSource`: paho 2.x client, subscribes in `on_connect`, reconnects, queues raw payloads with their receipt time |
| `payload/`                | `PayloadDecoder` (strategy) with `EmonV1Decoder`; `DecoderRegistry`: prefix → decoder (§3.3) |
| `signature.py`            | `Signature`: validation against the allowed prefixes; masking for logs |
| `pipeline.py`             | `Pipeline`: decode → parse → build points; reports problems |
| `telegram/frame.py`       | `TelegramFrame`: outer structure and CRC |
| `telegram/obis.py`        | `ObisLine`, `ObisValue` and the line lexer, including the multi-line DSMR 2.2 gas reading |
| `telegram/lines.py`       | `LineIndex`: sorts lines into electricity lines and M-Bus channels |
| `telegram/registry.py`    | `FieldSpec`, `DerivedSpec`, `MbusSpec`: OBIS code → field, unit, type, description. Single source for §4.3 |
| `telegram/electricity.py` | `ElectricityMapper`: registered electricity fields with unit conversion |
| `telegram/derived.py`     | one function per derived field (`DERIVERS`); a test checks they match the registry |
| `telegram/mbus.py`        | `MbusChannel`: one channel's device type and readings; `MbusMapper`: readings at their capture time |
| `telegram/parser.py`      | `TelegramParser`: coordinates the above into a `Reading` |
| `telegram/model.py`       | `Reading`, `MbusReading`, `FieldIssue` |
| `points.py`               | `PointBuilder`: `Reading` → line protocol |
| `fieldmap.py`             | `FieldMapPublisher`: writes `dsmr_fields` once per registry version |
| `diagnostics/`            | `ErrorReporter` (façade), `RateLimiter`, `ErrorLog`, `QuarantineStore`, `ErrorEvent` |
| `sink/writer.py`          | `Writer` protocol, `InfluxWriter`, `DryRunWriter` (null object), `WriteError` |
| `sink/delivery.py`        | `Delivery`: one write attempt and its outcome (written, retry, rejected) |
| `sink/backoff.py`         | `BackoffPolicy`: when to try again (1 s … 5 min) |
| `sink/replay.py`          | `SpoolReplayer`: sends spooled batches oldest first |
| `sink/spool.py`           | `DiskSpool`: write-ahead files in line protocol, size cap |
| `sink/sink.py`            | `InfluxSink`: batching and coordination of the above |
| `tools/`                  | `fields`, `replay`, `compare_buckets`, `fix_orientation` (§10, §13) |

**Design patterns.**

| Pattern | Where |
| --- | --- |
| Strategy | `PayloadDecoder` implementations; `Writer` implementations; one derive function per derived field |
| Null object | `DryRunWriter` |
| Registry / table-driven | `DecoderRegistry`; the OBIS field registry instead of a parser per DSMR version |
| Façade | `ErrorReporter` over rate limiting, error log and quarantine |
| Factory | `container.build()`; named constructors such as `Settings.from_env`, `Signature.parse` |
| Pipeline | `Pipeline` |
| Producer-consumer | the bounded queue between the MQTT thread and the worker |
| Store-and-forward | `DiskSpool` and `SpoolReplayer` |
| Retry with exponential backoff | `BackoffPolicy` |
| Value object | frozen dataclasses (`Payload`, `ObisLine`, `MbusReading`, `FieldSpec`, `ErrorEvent`) |

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
6. **Size cap.** The spool is capped at `SPOOL_MAX_MB`, default 6 GB, so it covers an outage of about **three weeks**.
   - Measured on 2026-11-17: 83 households, about 14,000 points per hour, about 600 bytes per point. That is about 200 MB per day.
   - Three weeks is then about 4.2 GB; the rest is room for more kits.
   - When the spool is full, the oldest files are dropped and the number of lost points is logged as an error.
   - The timestamp is fixed when the message is received and stored in the line protocol, so replayed points land at their original time.
7. **4xx responses are not retried.** A 4xx other than 429 means bad data or missing permission. The batch goes to the quarantine file and is reported.

Limit: messages that never arrive are lost. That happens when the parser itself or the MQTT connection is down, because the kits publish with QoS 0 and the broker does not keep them.

## 8. Errors and diagnostics

| Category          | Example                              | Effect                         |
| ----------------- | ------------------------------------ | ------------------------------ |
| `payload_json`    | corrupt bytes, invalid UTF-8         | message rejected               |
| `payload_shape`   | `1`, missing `datagram.p1`           | message rejected (logged at DEBUG; expected noise) |
| `payload_unsupported` | prefix without a decoder (V02 for now) | message rejected and quarantined |
| `signature`       | wrong prefix or format               | message rejected               |
| `crc`             | CRC mismatch                         | written with `crc_valid=false` (warning); rejected with `REQUIRE_VALID_CRC=true` |
| `telegram`        | no `/` or `!`, truncated             | message rejected               |
| `field`           | unparsable value, unexpected unit    | field skipped, rest written    |
| `mbus_time`       | capture time far from receipt time   | reading written at receipt time |
| `influx_rejected` | 4xx from InfluxDB                    | batch quarantined              |
| `spool_dropped`   | spool full                           | oldest points dropped          |

**Where logs go** (`logging_setup.py`). Each record has one destination, so nothing is logged twice:

| Logger | Content | Destination |
| ------ | ------- | ----------- |
| `emon2influx` | start, stop, outages, hourly summary | console (stderr, collected by journald) and `LOG_DIR/emon2influx-parser.log` |
| `emon2influx.errors` | problems per message | `LOG_DIR/errors.log` only; on the console only when `LOG_DIR` is empty |
| – | rejected messages | `QUARANTINE_FILE` |

All files rotate hourly; `LOG_KEEP` files are kept.

**Error log** (`logs/errors.log`). One record per error:
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
  - runs `/opt/emon2influx-parser/current/venv/bin/python -m emon2influx` (the release the updater installed, §11.1);
  - `EnvironmentFile=/etc/emon2influx-parser/env` (mode 600);
  - `StateDirectory` for the spool, `LogsDirectory` for logs;
  - `Restart=always`;
  - hardening options.
- **Target:** directly on an LXC container; no container images (decided 2026-11-16).
- **Switching over.**
  - Old and new parser can run side by side, each with its own client id.
  - Only one may write to `p1-smartmeters`.
  - The switch itself is a separate decision.

### 11.1 Automatic updates

- Status: **approved** (2026-11-16)

The LXC cannot be reached from the internet, so it fetches new versions itself (pull), like the
p1-meter-app (its deployment design). A merge into `main` reaches the LXC without manual steps.

```
 merge → main
      │
      ▼
 GitHub Actions                       GitHub Releases (public)              LXC
 ┌──────────────────────┐ upload ┌──────────────────────────────┐  HTTPS ┌─────────────────────────────────┐
 │ ruff, mypy, pytest   │──────▶ │ pre-release "main-latest"    │◀───────│ emon2influx-update.timer (1 min)│
 │ build wheel +        │        │  manifest.json               │  poll  │  download, sha256 check         │
 │ wheelhouse (deps)    │        │  emon2influx-parser-<sha>.tgz│        │  new venv, install offline      │
 └──────────────────────┘        └──────────────────────────────┘        │  switch symlink, restart        │
                                                                          │  health check, else rollback    │
                                                                          └─────────────────────────────────┘
```

**Release package (CI).** A job `package` runs only on a push to `main`, after the checks passed:

1. Build the wheel of `emon2influx-parser`.
2. Download all dependencies as wheels into a wheelhouse. They are all pure Python, so the package does not depend on the CPU architecture.
3. Pack both, plus `deploy/` (unit files and the update script), into `emon2influx-parser-<sha>.tar.gz`.
4. Write `manifest.json` with: `version`, `sha` (git commit), `sha256` of the tarball, `python` (required minor version, `3.12`), `created`.
5. Replace the assets of the rolling pre-release `main-latest`.

The version shown in the log becomes `<version>+<short sha>`, so every build is recognisable.

**Updater on the LXC.** The script `emon2influx-update` runs from `emon2influx-update.service` (root, oneshot), started by `emon2influx-update.timer` every minute. Each run:

1. Download `manifest.json` over HTTPS. Stop when its `sha` is the running one, or is listed as a bad release.
   - The URL is the direct download link `https://github.com/AvansETI/emon2influx-parser/releases/download/main-latest/manifest.json`, **not** the REST API.
   - Unauthenticated REST API calls are limited to 60 per hour per public IP address. The p1-meter-app updater already uses 30 per hour from the same network, every 2 minutes. Polling the API every minute would exceed the limit and delay the updates of both.
   - The download link is not under that limit. It costs about 1 kB per minute; the package itself is only downloaded when the commit differs.
2. Stop when the LXC's Python does not match `python`. This is logged as an error; Python is upgraded with the OS, not by this script.
3. Download the tarball and check its sha256 against the manifest. A mismatch stops the update.
4. Unpack to `/opt/emon2influx-parser/releases/<sha>/`, create a venv there and install from the wheelhouse **offline** (`--no-index`). Nothing is fetched from PyPI on the LXC.
5. Check the configuration with the new version, without connecting (`python -m emon2influx --check-config`). A configuration the new version rejects stops the update before the switch.
6. Switch the symlink `/opt/emon2influx-parser/current` to the new release atomically, then restart the service.
   - The parser stops cleanly on SIGTERM: queue drained, last batch written or spooled.
   - The spool is plain line protocol, so a new version replays what the old one left.
7. Health check (below), up to 90 s. On failure:
   - switch the symlink back and restart the previous release;
   - record the `sha` as bad, so the timer does not try it again;
   - log the reason as an error (journald).
8. Keep the last 3 releases; remove older ones.

**Health.** The parser has no HTTP endpoint. Instead it writes `/var/lib/emon2influx-parser/health.json` every 30 s, atomically:
- `version`, `started_at`, `updated_at`;
- `mqtt_connected`;
- `messages` since the start;
- `last_written_at`;
- `spool_files`.

A release counts as healthy when, within 90 s:
- the service is active;
- `health.json` shows the new version and was updated in the last 60 s;
- MQTT is connected;
- at least one message was processed.

An InfluxDB outage is **not** a reason to roll back: the spool keeps the data, and the old version would have the same problem.

**Timing.** After a merge, the new version runs after:

| Step | Duration |
| --- | --- |
| CI checks and package | about 2–3 min |
| until the timer runs | at most 1 min, about 30 s on average |
| install (offline) | about 30 s |
| restart | about 5–10 s, during which messages are not received (kits publish with QoS 0) |
| health check | up to 90 s |

That is about 3–5 minutes in total, mostly CI. The MQTT signal of issue #1 would remove the wait for the timer.

**Manual use.**
- `sudo emon2influx-update --now`: check now.
- `sudo emon2influx-update --rollback`: back to the previous release, which is then recorded as current.
- `journalctl -u emon2influx-update`: what the updater did.

**First installation.** `deploy/install.sh` is read first, then run once as root. It:
- creates the user, the directories and `/etc/emon2influx-parser/env` (from `.env.example`, mode 600, to be filled in);
- installs the update script and the units;
- enables the timer.

The first run of the updater then installs the current release.

**Security.**
- Downloads come only from `https://github.com/AvansETI/emon2influx-parser/releases`. The repository is public, so no token is needed.
- The sha256 in the manifest protects against corrupt or incomplete downloads, not against a compromised GitHub account. Branch protection on `main` limits who can release.
- Only the updater runs as root. The parser keeps its own unprivileged user and its hardening.
- No secrets are involved. `/etc/emon2influx-parser/env` is never touched by an update.

**Out of scope.**
- OS and Python upgrades.
- The MQTT release signal (issue #1).
- Changes to the env file.

## 12. Consequences for the p1-meter-app

These are not part of this project; they are listed here so they can be planned:

- **Swapped counters.** New data is correct from the switch on; history is corrected afterwards (§13). Until then a household may flip orientation at the switch date. The app's detection stays as a safety net.
- **Missing gas.** Households without gas get no `gas_delivered` at all instead of 0. The rule "gas only if `max > 0`" keeps working.
- **Gas at its capture time.** The app shifts hourly gas back by one hour (gas analysis §3.6). For data written by the new parser that shift must not be applied, or it will be double.
- **New fields** (voltage, current, per phase) can be used later.

## 13. Correcting swapped counters in history

The old parser wrote import as `energy_produced` and export as `energy_consumed` for telegrams
without a version line (§1). A separate tool, `python -m emon2influx.tools.fix_orientation`,
corrects that history once the new parser is live.

**How InfluxDB is changed.** InfluxDB cannot change a field in place. A point written with the
same measurement, tag set and timestamp overwrites the fields it contains, so the tool reads
the affected points and writes them back with `energy_consumed` and `energy_produced` swapped.
No other field is touched.

**Which periods.** A kit can move to another meter, so the orientation is decided per day, not per kit:

1. **Per day**, compare the integral of `power_consumed` (kWh) with the daily delta of each counter. The closer counter is the import counter. The app already detects orientation the same way.
2. **Days without a clear vote** get the orientation of their neighbours: too little use, too little data, or both counters close.
3. **Merge** the days into contiguous periods per signature.
4. **Only periods voted "swapped"** that end before the switch to the new parser are corrected.

**Safety.**

- **Dry-run first, and it is the default.** It lists the signatures and periods that would be corrected, with the evidence per period, and the number of points.
- **Backup.** Before writing, the affected series are exported as gzipped line protocol, so they can be restored.
- **Never swapped twice.**
  - Every correction is recorded in the measurement `dsmr_corrections`: tag `signature`, fields `from`, `to`, `kind` and `points`.
  - Recorded periods are skipped.
  - A corrected day votes "as named", so it is not selected again.
- **Batches.** Points are written in batches with retry and can be resumed. Tens of millions of points take hours.
- **Rehearsal.** The tool is tested on a copy of a few series in the test bucket first.
- **Production.** The production run needs the user's explicit approval and a write token the user sets. Development and tests never write to `p1-smartmeters`.

**Order.**
1. The new parser goes live, so no new swapped data arrives.
2. The history up to the switch is corrected.
3. The app keeps its orientation detection as a safety net.

**Usage** (`python -m emon2influx.tools.fix_orientation`):

| Command | What it does | Writes |
| --- | --- | --- |
| `plan --env F --until T` | votes per day per household and lists the swapped periods before `T`, plus segments to review; writes `plan.json` | no |
| `apply --env F --plan plan.json --yes` | backup, swap from the backup, record in `dsmr_corrections`, verify | yes |
| `verify --env F --plan plan.json` | the votes per period now | no |
| `restore --env F --backup FILE --yes` | writes the original values back and records the restore | yes |
| `copy --source-env F --env F --signature S --from A --until B` | copies a household's history into a test bucket for a rehearsal | test bucket only |

`--env F` is a file with `INFLUX_URL`, `INFLUX_ORG`, `INFLUX_BUCKET` and `INFLUX_TOKEN`. Writing to `p1-smartmeters` also needs `--allow-production`. `plan.json` and the backups contain full signatures and stay private (git-ignored).

**Implementation details:**

- **Daily statistics** are queried in blocks of 60 days. Years in one query exceed the reverse proxy's time-out. On the LXC, which reaches InfluxDB directly, a full plan is faster than through the proxy (about one minute per household with years of data).
- **The swap is computed from the backup**, not from the live data. An interrupted run that is started again writes the same values again; nothing is swapped back.
- **Stricter than the app's rule:**
  - only days with a margin of at least 0.2 kWh between the two counters vote;
  - gaps take the orientation of their neighbours only when both agree;
  - a swapped segment needs at least 3 clear days and an agreement of 80 %;
  - a short contradicting run between two equal neighbours is treated as noise.

**Rehearsal on 2026-11-16.** One week of a known swapped household was copied into the test bucket. Then:

1. `plan` found one period (6 clear days, 100 % agreement).
2. `apply` swapped 18,860 points; verify then voted as-named on all clear days.
3. A second `plan` found nothing.
4. A second `apply` skipped the period.
5. `restore` brought back the original values, and verify voted swapped again.

A read-only `plan` on `p1-smartmeters` for the same household found one period of 972 days (931 clear days, 100 % agreement).

### 13.1 Removing the zeros of households without a gas meter

- Status: **approved** (2026-11-16)

The old parser wrote `gas_delivered=0` when the telegram had no gas reading. A gas meter reports a rising reading, never 0. So a household whose gas was **0 in every point**, up to now, has no gas meter, and the new parser writes no gas for it at all.

**Why the maximum, not the last value.** Checking only the last value is not enough, for two reasons:
- the old parser also wrote 0 when it could not read the gas line, so a household with a gas meter has occasional zeros between real readings;
- a kit can move to another meter.

The plan therefore uses the maximum over the whole lifetime.

**Feed-in is not cleaned.** Telegrams always contain `2.8.x` and `2.7.0`, so a 0 there is a real reading, and the new parser keeps writing it.

**How.** InfluxDB 2.7 refuses to delete one field ("delete by field is not supported", tested 2026-11-16). `python -m emon2influx.tools.drop_zero_gas` therefore rewrites each affected household day by day:

1. back up all points of the day (gzipped line protocol, all fields and types);
2. delete the day (by signature and time, which InfluxDB supports);
3. write the points back without `gas_delivered`;
4. check that the number of points matches and no gas is left.

**Safety.**
- `plan` is read-only: households with gas points whose maximum is 0.
- During `apply`, each day's values are checked again. One gas value other than 0 stops that household. This costs nothing extra: the values are loaded for the backup anyway.
- The rewrite comes from the backup. An interrupted run resumes; finished days are skipped.
- `restore` writes a household's backups back as they were.
- The period of the new parser (from `--until`) is never touched.
- `dsmr_corrections` records `gas_zero_removed` and `gas_zero_restored`.
- Writing to `p1-smartmeters` needs `--yes` and `--allow-production`.

**Rehearsal on 2026-11-16.** Three days of a household without a gas meter were copied into the test bucket: 8,918 points per field, gas all 0. Then:

1. `plan` found that household only.
2. `apply` rewrote 3 days and removed 8,918 gas points; the four other fields kept all 8,918 points.
3. A second `apply` did nothing.
4. `restore` brought gas back to 8,918 points.

## 14. Open questions and decisions

| #   | Topic                 | Status                                                                                          |
| --- | --------------------- | ----------------------------------------------------------------------------------------------- |
| 1   | V02 kits payload      | **Open.** Different from V01, not yet known. A decoder is added when it is (§3.3). Proposed fields `sent_at`, `synced`, `seq`: [high-availability.md](high-availability.md) §3.2. |
| 2   | Approximated cos φ    | **Not done** (decided 2026-11-16). Not in DSMR; P/(U×I) is inaccurate with whole amperes.         |
| 3   | GitHub repository     | **Done** (2026-11-16). Public repository under AvansETI; releases feed the updater (§11.1).      |
| 4   | InfluxDB host         | **Done** (2026-11-16). Dev server: the public URL; production: the local address. Both are set in the env file only, never in the repository. |
| 5   | Field map in InfluxDB | **Done** (2026-11-16). `dsmr_fields` measurement next to the docs (§4.3).                         |
| 6   | High availability     | **Draft** (2026-11-17), issue #2. Two sites with identical points via a stamped receipt time: [high-availability.md](high-availability.md). |

Host names, addresses and tokens belong in the env file. Documentation uses placeholders such
as `influxdb.example.org` and `192.0.2.10`.
