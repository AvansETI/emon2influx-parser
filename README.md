# emon2influx-parser

Receives P1 smart-meter telegrams that EMON kits publish over MQTT, parses them and writes the
readings to InfluxDB. Successor of the original `emon2influx` script.

- Architecture and design: [doc/architecture.md](doc/architecture.md); classes: [doc/class-diagram.md](doc/class-diagram.md)
- Every stored field and its DSMR/OBIS origin: [doc/fields.md](doc/fields.md)

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env            # fill in; never commit .env
.venv/bin/pytest                # tests
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
.venv/bin/mypy                  # strict type check
```

After changing `src/emon2influx/telegram/registry.py`, regenerate the field docs (a test fails
otherwise):

```sh
.venv/bin/python -m emon2influx.tools.fields
```

## Running

```sh
.venv/bin/python -m emon2influx   # reads .env from the working directory
```

- `MODE=dry-run` (default) parses everything and writes nothing.
- `MODE=write` writes to `INFLUX_BUCKET`. Writing to the production bucket `p1-smartmeters`
  also needs `ALLOW_PRODUCTION_BUCKET=true`, which is only set on the production server.
- Logs: stderr, `LOG_DIR/emon2influx-parser.log`, errors in `LOG_DIR/errors.log`, rejected
  messages in `QUARANTINE_FILE` (identifiers removed). An hourly summary is in the main log.
- When InfluxDB is unreachable, batches are kept in `SPOOL_DIR` and replayed later.

## Testing against old logs

Replays logs of the old `emon2influx` through the new parser and reports the differences per kit
(signatures masked). Nothing is written.

```sh
.venv/bin/python -m emon2influx.tools.replay /path/to/emon2influx.log
```

## Deployment (systemd)

```sh
sudo useradd --system --no-create-home emon2influx
sudo git clone <repo> /opt/emon2influx-parser
sudo python3 -m venv /opt/emon2influx-parser/.venv
sudo /opt/emon2influx-parser/.venv/bin/pip install /opt/emon2influx-parser
sudo install -d -m 700 -o emon2influx /etc/emon2influx-parser
sudo install -m 600 -o emon2influx .env.example /etc/emon2influx-parser/env   # then fill in
sudo cp deploy/emon2influx-parser.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now emon2influx-parser
journalctl -u emon2influx-parser -f
```

