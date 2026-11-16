"""`python -m emon2influx`: loads `.env` from the working directory, then runs the service."""

import sys

from dotenv import load_dotenv

from .app import main_with
from .config import ConfigError, Settings


def main() -> int:
    # Variables already in the environment (systemd EnvironmentFile) win over .env.
    load_dotenv(".env", override=False)
    try:
        settings = Settings.from_env()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    return main_with(settings)


if __name__ == "__main__":
    sys.exit(main())
