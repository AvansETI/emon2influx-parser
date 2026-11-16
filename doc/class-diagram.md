# Class diagram

The classes of `emon2influx`, grouped by responsibility, and how a message flows through them.
See [architecture.md](architecture.md) §6 for the module table and the design patterns.
Diagrams are [Mermaid](https://mermaid.js.org/) and render on GitHub.

## Overview

Every class has one responsibility. `build()` in `container.py` creates and connects them;
`App` only orders start and stop.

```mermaid
classDiagram
    direction LR

    class App {
        +run() int
        +request_stop(signum, frame)
    }
    class Components {
        <<dataclass>>
        settings, metrics, reporter, messages
        source, pipeline, spool, sink
        summary, field_map
    }
    class container {
        <<module>>
        +build(settings, writer) Components
    }
    class Worker {
        +run(stop)
        +drain() int
    }
    class MqttSource {
        +start()
        +stop()
    }
    class Pipeline {
        +process(raw, received_at) list
    }
    class InfluxSink {
        +add(lines)
        +start()
        +tick()
        +close()
    }
    class SummaryReporter {
        +maybe_report()
        +report() str
    }
    class FieldMapPublisher {
        +publish() bool
    }
    class Metrics {
        +incr(name, amount)
        +error(category, amount)
        +kit(signature)
        +unknown_codes(codes)
        +snapshot(reset) MetricsSnapshot
    }
    class ErrorReporter {
        +report(event, written, raw, quarantine, count)
    }
    class logging_setup {
        <<module>>
        +setup_logging(settings)
    }

    container ..> Components : creates
    App --> Components
    App *-- Worker
    App ..> logging_setup : main_with()
    Worker --> Pipeline
    Worker --> InfluxSink
    Worker --> SummaryReporter
    MqttSource ..> Worker : queue of messages
    Pipeline --> ErrorReporter
    Pipeline --> Metrics
    InfluxSink --> Metrics
    InfluxSink --> ErrorReporter
    ErrorReporter --> Metrics
    SummaryReporter --> Metrics
    App --> FieldMapPublisher
```

## Payload decoding

One decoder per kit generation (strategy); the registry picks it and checks it belongs to the
signature's prefix (architecture §3.3).

```mermaid
classDiagram
    class DecoderRegistry {
        +prefixes: tuple
        +decoders: Mapping
        +decode(raw, received_at) tuple
    }
    class PayloadDecoder {
        <<abstract>>
        +name: str
        +matches(message) bool
        +decode(message, received_at) Payload
    }
    class EmonV1Decoder
    class Payload {
        <<value object>>
        +signature_raw: str
        +p1: str
        +received_at: datetime
    }
    class Signature {
        <<value object>>
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

    DecoderRegistry o-- PayloadDecoder
    PayloadDecoder <|-- EmonV1Decoder
    PayloadDecoder ..> Payload : creates
    DecoderRegistry ..> Signature : validates
    DecoderRegistry ..> RejectedMessage : raises
```

## Telegram parsing

`TelegramParser` only coordinates. Fields are found by OBIS code through the registry; a bad
line becomes a `FieldIssue` and the rest of the telegram is kept (architecture §3.4, §4).

```mermaid
classDiagram
    direction LR
    class TelegramParser {
        +parse(text, received_at) Reading
    }
    class TelegramFrame {
        <<value object>>
        +identification: str
        +lines: list
        +crc_valid: bool
        +manufacturer: str
        +parse(text, require_valid_crc)$ TelegramFrame
    }
    class LineIndex {
        +electricity: dict
        +channels: dict
        +issues: list
        +build(lines)$ LineIndex
        +first_text(code) str
    }
    class ObisLine {
        <<value object>>
        +code: str
        +generic_code: str
        +values: tuple
    }
    class ElectricityMapper {
        +map(index) tuple
    }
    class derived {
        <<module>>
        +DERIVERS: dict
        +derive_all(ctx) dict
    }
    class DeriveContext {
        frame, index, received_at
        meter_time, version, fields, issues
    }
    class MbusChannel {
        +channel: int
        +device_type: int
        +readings: list
        +from_lines(channel, lines)$ MbusChannel
        +spec_for(unit) MbusSpec
    }
    class MbusMapper {
        +max_capture_skew: timedelta
        +map(channels, received_at) tuple
    }
    class Reading {
        +electricity: dict
        +mbus: list
        +issues: list
        +unknown_codes: list
    }
    class registry {
        <<module>>
        +ELECTRICITY: FieldSpec list
        +DERIVED: DerivedSpec list
        +MBUS: MbusSpec list
        +EXCLUDED: dict
        +field_map() list
    }

    TelegramParser ..> TelegramFrame
    TelegramParser ..> LineIndex
    LineIndex o-- ObisLine
    TelegramParser --> ElectricityMapper
    TelegramParser ..> derived
    derived ..> DeriveContext
    TelegramParser --> MbusMapper
    MbusMapper ..> MbusChannel
    TelegramParser ..> Reading : creates
    ElectricityMapper ..> registry
    derived ..> registry
    MbusChannel ..> registry
```

## Diagnostics

`ErrorReporter` is a façade: it counts the event, lets the rate limiter decide whether it is
logged, writes it to the error log and, for rejected messages, to the quarantine file.

```mermaid
classDiagram
    class ErrorReporter {
        +create(metrics, limit_per_hour, quarantine_file)$ ErrorReporter
        +report(event, written, raw, quarantine, count)
    }
    class ErrorEvent {
        <<value object>>
        +category: Category
        +message: str
        +signature: str
        +meter: str
        +version: str
        +line: str
    }
    class RateLimiter {
        +decide(signature, category) Decision
    }
    class ErrorLog {
        +level(event, written)$ int
        +write(event, level, limit_reached)
    }
    class QuarantineStore {
        +add(event, raw)
    }
    class Metrics

    ErrorReporter --> Metrics
    ErrorReporter --> RateLimiter
    ErrorReporter --> ErrorLog
    ErrorReporter --> QuarantineStore
    ErrorReporter ..> ErrorEvent
```

## Writing and buffering

```mermaid
classDiagram
    direction LR
    class InfluxSink {
        +batch_size: int
        +outage_since: float
        +add(lines)
        +tick(final)
        +close()
    }
    class Delivery {
        +last_error: WriteError
        +send(batch) Outcome
    }
    class Writer {
        <<protocol>>
        +write(lines)
        +close()
    }
    class InfluxWriter
    class DryRunWriter {
        <<null object>>
    }
    class BackoffPolicy {
        +delay_s: float
        +ready() bool
        +failed() float
        +succeeded()
    }
    class SpoolReplayer {
        +replay(budget_s) bool
    }
    class DiskSpool {
        +append(lines) int
        +oldest() Path
        +remove(path)
    }
    class WriteError {
        <<exception>>
        +retryable: bool
        +status: int
    }

    InfluxSink --> Delivery
    InfluxSink *-- BackoffPolicy
    InfluxSink *-- SpoolReplayer
    InfluxSink --> DiskSpool
    SpoolReplayer --> DiskSpool
    SpoolReplayer --> Delivery
    SpoolReplayer --> BackoffPolicy
    Delivery --> Writer
    Writer <|.. InfluxWriter
    Writer <|.. DryRunWriter
    InfluxWriter ..> WriteError : raises
```

## Message flow

```mermaid
sequenceDiagram
    participant B as MQTT broker
    participant S as MqttSource
    participant W as Worker
    participant P as Pipeline
    participant K as InfluxSink
    participant D as DiskSpool
    participant I as InfluxDB

    B->>S: message
    S->>W: queue (bytes, received_at)
    W->>P: process()
    P->>P: decode, parse, build points
    P-->>W: line protocol
    W->>K: add(lines)
    K->>I: write batch (Delivery)
    alt InfluxDB unreachable
        K->>D: append(batch)
        loop BackoffPolicy: 1 s to 5 min
            K->>I: SpoolReplayer sends the oldest file
        end
        K->>D: remove(file)
    end
```

## Where logs go

| Logger | Content | Destination |
| ------ | ------- | ----------- |
| `emon2influx` | start, stop, outages, hourly summary | console (journald) and `emon2influx-parser.log` |
| `emon2influx.errors` | problems per message | `errors.log` only |
| `QuarantineStore` | rejected messages, identifiers removed | `quarantine.jsonl` |
