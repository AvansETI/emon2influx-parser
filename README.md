# emon2influx-parser

Receives P1 smart-meter telegrams that EMON kits publish over MQTT, parses them and writes the
readings to InfluxDB. Successor of the original `emon2influx` script.

- Architecture and design: [doc/architecture.md](doc/architecture.md)
- Configuration: copy `.env.example` to `.env` and fill it in. Never commit `.env`.

Status: design phase; no code yet.
