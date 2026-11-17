# High availability on two sites (design sketch)

Status: **draft, not approved** (2026-11-17). Nothing in this document is built yet.

Goal: two physically separate sites, each with an MQTT broker, InfluxDB, the parser and the
p1-meter app. When a component on one site fails, the other site keeps working. After
recovery both sites hold the same data again, without manual work.

Host names and addresses below are placeholders (`site-a.example.org`, `192.0.2.x`).

## 1. Overview

```
            kits (QoS 0, one broker host name)
                 │                       │
        DNS failover: mqtt.example.org   │
                 ▼                       ▼
   ┌──────────── site A ──────────┐   ┌──────────── site B ──────────┐
   │ broker ── stamper            │   │ stamper ── broker            │
   │   │  stamped/# ◄── bridge ──────────► stamped/#   │             │
   │ parser ──► InfluxDB ◄── reconcile ──► InfluxDB ◄── parser       │
   │ p1-meter app (reads local)   │   │ p1-meter app (reads local)   │
   └──────────────────────────────┘   └──────────────────────────────┘
                 ▲                                       ▲
                 └──── DNS failover: p1-meter.example.org ┘
```

The two sites are connected by a VPN tunnel (for example WireGuard) for the bridge and the
reconcile job.

## 2. Principle: identical points everywhere

The design depends on one property: **one telegram becomes exactly the same point on both
sites** (same series, same timestamp, same fields). InfluxDB then overwrites instead of
duplicating, so writing twice, replaying and copying are all harmless. Time series are only
appended to, so there are no conflicting edits and no split-brain.

This property does **not** hold today:

- The electricity point is stamped with the parser's receipt time (§5 of
  [architecture.md](architecture.md)). Two parsers receive the same telegram a few
  milliseconds apart, which gives two near-duplicate points.
- A message that waits in the bridge queue during a link failure is received late; on the
  other site its point lands at the wrong time and its `clock_offset_s` is wrong.

M-Bus points (gas, water, heat) already use the capture time from the telegram and are
identical on both sites.

## 3. One timestamp per telegram

The receipt time is kept as the source of truth, because meter clocks drift up to about a
minute (§5 of architecture.md). It is fixed **once**, at the first broker that receives the
message, and travels with the message from then on.

### 3.1 Stamper (works with every kit)

- A small service per site subscribes to the raw kit topic (`smartmeter/raw`) on the local
  broker.
- It republishes each message unchanged on `stamped/smartmeter/raw`, with the receipt time as
  an MQTT 5 user property `received_at` (UTC, ISO 8601, millisecond precision) and
  `site=<site id>`.
- The bridge carries **only** `stamped/#`, in both directions. The raw topic is never
  bridged, so a message is stamped exactly once and cannot loop.
- The parser subscribes to `stamped/smartmeter/raw` and uses `received_at` from the property
  instead of its own clock. A message without the property (stamper not deployed) falls back
  to the local receipt time, as today.
- The stamper can be a role of the parser package (`python -m emon2influx.stamper`) so it
  shares configuration, logging and auto-update; it has no InfluxDB dependency.
- Requires MQTT 5 on both brokers and on the bridge (Mosquitto 2.x supports this).

Alternative if user properties prove impractical: wrap the original payload in an envelope
`{"received_at": ..., "site": ..., "payload": <original>}`. The decoder registry already
isolates payload formats, so this is one extra unwrapping step.

### 3.2 Time from the kit (V02 payload)

The V02 payload is not defined yet (architecture.md §14, #1). This is the moment to include:

| Field     | Content                                                                 |
| --------- | ----------------------------------------------------------------------- |
| `sent_at` | Send time from the kit's NTP-synchronised clock, UTC, millisecond epoch |
| `synced`  | `true` if the kit's clock was synchronised within the last hour         |
| `seq`     | Message counter, restarting at boot; detects lost or duplicate messages |

The parser then chooses the timestamp in this order:

1. `sent_at` from the kit, if `synced` is true and it is within a plausible window
   (for example 10 minutes) of the receipt time;
2. `received_at` from the stamper;
3. the parser's own receipt time.

With 1, V02 kits no longer depend on the stamper; V01 kits keep using 2. The stamper stays
until no V01 kits are left. The firmware change belongs in the kit repository, not here.

## 4. Components per layer

### 4.1 MQTT

- Mosquitto on both sites, same users and passwords (kept in each site's config, not in git).
- Bridge from A to B over the VPN: MQTT 5, QoS 1, `cleansession false`, topic
  `stamped/# both 1`.
- While the link is down, the broker queues bridged messages up to its queue limit
  (`max_queued_messages`, `max_queued_bytes`). Thanks to the stamp they keep their correct
  time when delivered late. Longer gaps are filled by the reconcile job (§4.3).

### 4.2 Parser

- One per site; reads the local broker, writes the local InfluxDB.
- The spool covers outages of the local InfluxDB. At the current load it grows about
  200 MB per day; the default `SPOOL_MAX_MB=6144` covers about three weeks
  (architecture.md §7).
- Configuration that changes the points (for example `REQUIRE_VALID_CRC`) must be equal on
  both sites, or the points differ.
- Auto-update staggered: one site installs a release first, the other some hours later, so a
  bad release never hits both at once.

### 4.3 InfluxDB and the reconcile job

- InfluxDB OSS 2.x has no clustering. Its one-way replication is not used in both directions.
- A reconcile job on each site, for example hourly:
  1. counts points per household per hour on both sites for the last N days;
  2. copies the hours that are missing or short on one side from the other side.
- Because points are identical (§2), copying is idempotent and safe to repeat. The job covers
  outages of any length: a site that was down for weeks is filled from the other.
- Builds on `tools.compare_buckets` and `tools.fix_orientation copy`.
- **History corrections** (`fix_orientation`, `drop_zero_gas`) must not be undone by the job.
  Either the tools always run against both sites, or the job skips periods recorded in
  `dsmr_corrections`. Removed gas is the risky case: a missing field looks like a gap.

### 4.4 p1-meter app

- Stateless: in-memory cache, settings in the browser. Each site runs the app against its
  local InfluxDB.
- Per site: public IP address, reverse proxy, TLS certificate (ACME DNS-01 works on both).
- Rate limiting is per instance; acceptable.

### 4.5 Failover

- **App users:** DNS with health checks and a low TTL (a provider such as Cloudflare Load
  Balancing). Switching takes about the TTL, 1–5 minutes.
- **Kits:** they have one broker host name in the firmware.
  - Option A: the same DNS failover on the MQTT host name. Works only if the kit resolves
    the name again on reconnect; to be tested per kit generation.
  - Option B: firmware with two broker hosts. More reliable, but every kit needs an update.
- Messages sent while a kit reaches no broker are lost; the kits keep nothing. Two sites make
  this gap smaller, not zero.

## 5. Requirements

| Per site                                                       | Between the sites                                |
| -------------------------------------------------------------- | ------------------------------------------------ |
| Host (Proxmox or similar) with containers for broker, stamper, InfluxDB, parser, app | VPN tunnel                     |
| Public IP, reverse proxy, TLS certificate                      | DNS provider with health checks and failover     |
| Storage for the full history; both sites keep everything       | Monitoring that alerts when a site degrades      |
| Independent power and internet connection                      | Same MQTT users; separate InfluxDB tokens        |

## 6. Steps

Each step is useful on its own.

1. **Stamper and parser support** on the current single site (§3.1). No visible change, but
   it makes points reproducible.
2. **V02 payload** with `sent_at`, `synced`, `seq` (§3.2), as soon as the V02 format is
   defined.
3. **Warm standby:** site B with broker, bridge, parser, InfluxDB and the reconcile job. Data
   exists twice and stays in sync; switching over is manual.
4. **App on both sites** with DNS failover.
5. **Kit failover** via DNS or firmware. The largest uncertainty.

Prerequisite for all of this: the new parser has replaced the old one, which stamps points
differently.

## 7. Open questions

- Do V01 and V02 kits resolve the broker host name again after a lost connection?
- Which DNS provider, and is its health-check failover available on the current plan?
- Where is site B, and does it have its own power and internet connection?
- The bridge queue limits that fit the broker's memory.
