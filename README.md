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

## Correcting swapped counters in history

One-off, after the switch to this parser (doc/architecture.md §13):

```sh
python -m emon2influx.tools.fix_orientation plan  --env read.env  --until <switch time>
python -m emon2influx.tools.fix_orientation apply --env write.env --plan plan.json --yes \
    --allow-production
```

`plan` is read-only. `apply` backs up every period first; `restore --backup FILE --yes` undoes
it. Rehearse with `copy` into the test bucket first.

## Deployment

Directly on an LXC (Ubuntu 24.04, Python 3.12) with systemd; no container images. The LXC
fetches new releases itself, so it needs no inbound access (doc/architecture.md §11.1).

- **Release:** every push to `main` that passes CI publishes a package to the rolling
  pre-release `main-latest` (`deploy/package.sh`, `deploy/publish.sh`).
- **Update:** `emon2influx-update.timer` checks every minute. A new release is installed next to
  the running one, the service restarts, and without a healthy `health.json` within 90 s the
  previous release comes back.

First installation, once, as root:

```sh
curl -fsSLO https://raw.githubusercontent.com/AvansETI/emon2influx-parser/main/deploy/install.sh
less install.sh                        # read it first
sudo bash install.sh
sudo nano /etc/emon2influx-parser/env  # fill in; never commit or share it
sudo emon2influx-update                # installs the current release and starts the service
```

Day to day:

```sh
journalctl -u emon2influx-parser -f    # the parser
journalctl -u emon2influx-update       # what the updater did
sudo emon2influx-update                # check for a release now
sudo emon2influx-update --rollback     # back to the previous release
cat /var/lib/emon2influx-parser/health.json
```

## License

Copyright 2026 Arno Broeders.

Licensed under the [Apache License, Version 2.0](LICENSE): you may use, modify and
distribute this software freely, provided you keep the [NOTICE](NOTICE) file and credit the
author.
