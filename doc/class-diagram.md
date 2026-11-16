# Class diagram

The main classes of `emon2influx` and how a message flows through them. See
[architecture.md](architecture.md) §6 for the responsibilities. Diagrams are
[Mermaid](https://mermaid.js.org/) and render on GitHub and in most Markdown viewers.

## Overview

```mermaid
classDiagram
    direction LR

    class App {
        +settings: Settings
        +run() int
        +request_stop(signum, frame)
        -_write_field_map()
        -_summary()
    }
    class Settings {
        <<frozen dataclass>>
        +mqtt, influx, spool and log settings
        +signature_prefixes: tuple
        +require_valid_crc: bool
        +mode: Mode
        +from_env(env)$ Settings
    }
    class MqttSource {
        +start()
        +stop()
        -_on_connect()
        -_on_message()
    }
    class Pipeline {
        +stats: Counter
        +kits: set
        +unknown_codes: Counter
        +process(raw, received_at) list~str~
    }
    class DecoderRegistry {
        +prefixes: tuple
        +decode(raw, received_at) tuple
    }
    class TelegramParser {
        +require_valid_crc: bool
        +parse(text, received_at) Reading
    }
    class PointBuilder {
        +measurement: str
        +build(signature, reading) list~str~
    }
    class ErrorReporter {
        +report(category, message, ...)
        +take_counts() Counter
    }
    class InfluxSink {
        +add(lines)
        +start()
        +tick()
        +close()
    }
    class DiskSpool {
        +append(lines) int
        +oldest() Path
        +read(path)$ list~str~
        +remove(path)
    }
    class Writer {
        <<protocol>>
        +write(lines)
        +close()
    }
    class InfluxWriter
    class DryRunWriter

    App *-- Settings
    App *-- MqttSource
    App *-- Pipeline
    App *-- InfluxSink
    App *-- ErrorReporter
    MqttSource ..> App : queue of (bytes, received_at)
    Pipeline --> DecoderRegistry
    Pipeline --> TelegramParser
    Pipeline --> PointBuilder
    Pipeline --> ErrorReporter
    InfluxSink --> Writer
    InfluxSink *-- DiskSpool
    InfluxSink --> ErrorReporter
    Writer <|.. InfluxWriter
    Writer <|.. DryRunWriter
```

## Payload decoding

One decoder per kit generation; the registry picks it and checks that it belongs to the
signature's prefix (architecture §3.3).

```mermaid
classDiagram
    class DecoderRegistry {
        +prefixes: tuple
        +decoders: Mapping~str, PayloadDecoder~
        +decode(raw, received_at) tuple
    }
    class PayloadDecoder {
        <<abstract>>
        +name: str
        +matches(message) bool
        +decode(message, received_at) Payload
    }
    class EmonV1Decoder {
        +name = "emon-v1"
    }
    class Payload {
        <<frozen dataclass>>
        +signature_raw: str
        +p1: str
        +received_at: datetime
    }
    class Signature {
        <<frozen dataclass>>
        +value: str
        +prefix: str
        +parse(raw, prefixes)$ Signature
        +masked() str
    }
    class RejectedMessage {
        <<exception>>
        +category: Category
        +message: str
        +line: str
    }
    class Category {
        <<enumeration>>
        PAYLOAD_JSON
        PAYLOAD_SHAPE
        PAYLOAD_UNSUPPORTED
        SIGNATURE
        CRC
        TELEGRAM
        FIELD
        MBUS_TIME
        INFLUX_REJECTED
        SPOOL_DROPPED
        QUEUE_DROPPED
    }

    DecoderRegistry o-- PayloadDecoder
    PayloadDecoder <|-- EmonV1Decoder
    PayloadDecoder ..> Payload : creates
    DecoderRegistry ..> Signature : validates
    DecoderRegistry ..> RejectedMessage : raises
    RejectedMessage --> Category
```

## Telegram parsing

Fields are recognised by OBIS code through the registry; one bad line becomes a `FieldIssue`
instead of failing the telegram (architecture §3.4, §4).

```mermaid
classDiagram
    class TelegramParser {
        +parse(text, received_at) Reading
        -_meter_time()
        -_electricity()
        -_totals()
        -_mbus()
    }
    class TelegramFrame {
        <<frozen dataclass>>
        +identification: str
        +lines: list~str~
        +crc: int
        +crc_valid: bool
        +manufacturer: str
        +parse(text, require_valid_crc)$ TelegramFrame
    }
    class ObisLine {
        <<frozen dataclass>>
        +medium: int
        +channel: int
        +item: str
        +values: tuple~ObisValue~
        +code: str
        +generic_code: str
    }
    class ObisValue {
        <<frozen dataclass>>
        +text: str
        +unit: str
    }
    class Reading {
        <<dataclass>>
        +received_at: datetime
        +manufacturer: str
        +version: str
        +meter_time: datetime
        +electricity: dict
        +mbus: list~MbusReading~
        +issues: list~FieldIssue~
        +unknown_codes: list~str~
    }
    class MbusReading {
        <<frozen dataclass>>
        +field: str
        +value: float
        +captured_at: datetime
        +channel: int
    }
    class FieldIssue {
        <<frozen dataclass>>
        +category: str
        +message: str
        +line: str
    }
    class registry {
        <<module>>
        +ELECTRICITY: tuple~FieldSpec~
        +DERIVED: tuple~DerivedSpec~
        +MBUS: tuple~MbusSpec~
        +EXCLUDED: dict
        +field_map() list
        +registry_hash() str
    }
    class FieldSpec {
        <<frozen dataclass>>
        +obis: str
        +field: str
        +unit: str
        +kind: Kind
        +units: dict
    }
    class DerivedSpec {
        <<frozen dataclass>>
        +field: str
        +sources: tuple
    }
    class MbusSpec {
        <<frozen dataclass>>
        +device_type: int
        +field: str
        +obis: tuple
        +units: dict
    }

    TelegramParser ..> TelegramFrame : parses
    TelegramParser ..> ObisLine : lexes
    ObisLine *-- ObisValue
    TelegramParser ..> Reading : creates
    Reading *-- MbusReading
    Reading *-- FieldIssue
    TelegramParser --> registry
    registry *-- FieldSpec
    registry *-- DerivedSpec
    registry *-- MbusSpec
```

## Writing and buffering

```mermaid
classDiagram
    class InfluxSink {
        +batch_size: int
        +flush_interval_s: float
        +outage_since: float
        +stats: dict
        +add(lines)
        +tick(final)
        +close()
        -_send(batch)
        -_replay(budget_s)
        -_to_spool(batch)
    }
    class DiskSpool {
        +directory: Path
        +max_bytes: int
        +size_bytes: int
        +append(lines) int
        +oldest() Path
        +remove(path)
    }
    class WriteError {
        <<exception>>
        +retryable: bool
        +status: int
    }
    class InfluxWriter {
        +write(lines)
        +close()
    }
    class FieldMapState {
        +is_current() bool
        +mark_written()
    }

    InfluxSink *-- DiskSpool
    InfluxSink --> InfluxWriter
    InfluxWriter ..> WriteError : raises
    App ..> FieldMapState : dsmr_fields once per registry version
```

## Message flow

```mermaid
sequenceDiagram
    participant B as MQTT broker
    participant S as MqttSource
    participant A as App (worker)
    participant P as Pipeline
    participant K as InfluxSink
    participant D as DiskSpool
    participant I as InfluxDB

    B->>S: message
    S->>A: queue (bytes, received_at)
    A->>P: process()
    P->>P: decode → parse → build points
    P-->>A: line protocol
    A->>K: add(lines)
    K->>I: write batch
    alt InfluxDB unreachable
        K->>D: append(batch)
        loop backoff 1 s … 5 min
            K->>I: replay oldest file
        end
        K->>D: remove(file)
    end
```

## Tools

| Module                         | Purpose                                                       |
| ------------------------------ | ------------------------------------------------------------- |
| `tools.fields`                 | writes `doc/fields.md` and `doc/fields.json` from the registry |
| `tools.replay`                 | replays old logs through the new parser and compares          |
| `tools.compare_buckets`        | compares the reference bucket with the test bucket per kit    |
| `tools.fix_orientation` (next) | one-off correction of swapped counters in history (§13)       |
