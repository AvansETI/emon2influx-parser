"""Where log records go (doc/architecture.md §8). Each record has one destination:

- `emon2influx` (operational: start, stop, outages, hourly summary): console and
  `LOG_DIR/emon2influx-parser.log`;
- `emon2influx.errors` (problems per message): `LOG_DIR/errors.log` only. It does not propagate,
  so an error is not repeated in the main log or on the console. Without `LOG_DIR` it goes to
  the console, so nothing is lost.

The console is stderr, which journald collects under systemd.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from .config import Settings

FORMAT = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S")


def _rotating(path: str, keep: int) -> logging.Handler:
    handler = TimedRotatingFileHandler(path, when="H", backupCount=keep, utc=True)
    handler.setFormatter(FORMAT)
    return handler


def setup_logging(settings: Settings) -> None:
    main = logging.getLogger("emon2influx")
    errors = logging.getLogger("emon2influx.errors")
    for logger in (main, errors):
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
    main.setLevel(settings.log_level)
    errors.setLevel(settings.log_level)
    errors.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(FORMAT)
    main.addHandler(console)
    if settings.log_dir:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        main.addHandler(
            _rotating(str(settings.log_dir / "emon2influx-parser.log"), settings.log_keep)
        )
        errors.addHandler(_rotating(str(settings.log_dir / "errors.log"), settings.log_keep))
    else:
        errors.addHandler(console)
