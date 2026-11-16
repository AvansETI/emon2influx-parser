"""`python -m emon2influx`: loads the settings, then runs the service.

python -m emon2influx                    run (reads .env from the working directory)
python -m emon2influx --check-config     validate the settings and exit (0 = valid)
python -m emon2influx --version          print the version and exit
--env-file PATH                          read settings from PATH instead of .env
"""

import argparse
import sys

from dotenv import load_dotenv

from . import build_version
from .config import ConfigError, Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m emon2influx",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate the settings without connecting, then exit",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument("--env-file", default=".env", help="settings file (default .env)")
    args = parser.parse_args(argv)
    if args.version:
        print(build_version())
        return 0

    # Variables already in the environment (systemd EnvironmentFile) win over the file.
    load_dotenv(args.env_file, override=False)
    try:
        settings = Settings.from_env()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    if args.check_config:
        print(f"configuration valid (mode={settings.mode}, bucket={settings.influx_bucket or '-'})")
        return 0

    from .app import main_with

    return main_with(settings)


if __name__ == "__main__":
    sys.exit(main())
