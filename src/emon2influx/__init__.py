"""emon2influx-parser: P1 telegrams from EMON kits (MQTT) to InfluxDB."""

import json
from importlib import resources

__version__ = "0.1.0"


def build_version() -> str:
    """`0.1.0+<short sha>` for packages built by CI (doc/architecture.md §11.1), else `0.1.0`."""
    try:
        build = json.loads(resources.files(__package__).joinpath("build.json").read_text())
        return f"{__version__}+{str(build['sha'])[:7]}"
    except (OSError, ValueError, KeyError):
        return __version__
