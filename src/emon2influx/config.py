"""Settings from the environment (.env in development, EnvironmentFile under systemd).

Every setting is validated at start; all problems are reported at once (doc/architecture.md §9).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from .signature import valid_prefix

Mode = Literal["write", "dry-run"]


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("invalid configuration:\n  - " + "\n  - ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Settings:
    mqtt_host: str
    mqtt_port: int
    mqtt_topic: str
    mqtt_username: str
    mqtt_password: str = field(repr=False)
    mqtt_client_id: str
    mqtt_qos: int
    mqtt_tls: bool
    mqtt_clean_session: bool

    influx_url: str
    influx_org: str
    influx_bucket: str
    influx_token: str = field(repr=False)
    influx_measurement: str
    influx_batch_size: int
    influx_flush_interval_ms: int

    spool_dir: Path
    spool_max_mb: int

    signature_prefixes: tuple[str, ...]
    require_valid_crc: bool

    mode: Mode

    log_level: str
    log_dir: Path | None
    log_keep: int
    quarantine_file: Path | None
    error_log_limit_per_hour: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        reader = _Reader(os.environ if env is None else env)
        mode = cast(Mode, reader.choice("MODE", ("write", "dry-run"), "dry-run"))
        writing = mode == "write"
        settings = cls(
            mqtt_host=reader.text("MQTT_HOST"),
            mqtt_port=reader.integer("MQTT_PORT", 1883, 1, 65535),
            mqtt_topic=reader.text("MQTT_TOPIC", "smartmeter/raw"),
            mqtt_username=reader.text("MQTT_USERNAME", ""),
            mqtt_password=reader.text("MQTT_PASSWORD", ""),
            mqtt_client_id=reader.text("MQTT_CLIENT_ID", "emon2influx-parser"),
            mqtt_qos=reader.integer("MQTT_QOS", 1, 0, 2),
            mqtt_tls=reader.boolean("MQTT_TLS", False),
            mqtt_clean_session=reader.boolean("MQTT_CLEAN_SESSION", True),
            # Only needed when writing; dry-run works without InfluxDB.
            influx_url=reader.text("INFLUX_URL", None if writing else ""),
            influx_org=reader.text("INFLUX_ORG", None if writing else ""),
            influx_bucket=reader.text("INFLUX_BUCKET", None if writing else ""),
            influx_token=reader.text("INFLUX_TOKEN", None if writing else ""),
            influx_measurement=reader.text("INFLUX_MEASUREMENT", "dsmr"),
            influx_batch_size=reader.integer("INFLUX_BATCH_SIZE", 500, 1, 50_000),
            influx_flush_interval_ms=reader.integer("INFLUX_FLUSH_INTERVAL_MS", 1000, 50, 60_000),
            spool_dir=Path(reader.text("SPOOL_DIR", "spool")),
            spool_max_mb=reader.integer("SPOOL_MAX_MB", 2048, 1, 1_000_000),
            signature_prefixes=reader.prefixes("SIGNATURE_PREFIXES", "2019-ETI-EMON-V01"),
            require_valid_crc=reader.boolean("REQUIRE_VALID_CRC", False),
            mode=mode,
            log_level=reader.choice(
                "LOG_LEVEL", ("DEBUG", "INFO", "WARNING", "ERROR"), "INFO", upper=True
            ),
            log_dir=reader.optional_path("LOG_DIR", "logs"),
            log_keep=reader.integer("LOG_KEEP", 48, 1, 10_000),
            quarantine_file=reader.optional_path("QUARANTINE_FILE", "logs/quarantine.jsonl"),
            error_log_limit_per_hour=reader.integer("ERROR_LOG_LIMIT_PER_HOUR", 5, 0, 10_000),
        )
        if (
            writing
            and settings.influx_bucket == "p1-smartmeters"
            and not reader.boolean("ALLOW_PRODUCTION_BUCKET", False)
        ):
            reader.problems.append(
                "INFLUX_BUCKET is the production bucket; set ALLOW_PRODUCTION_BUCKET=true "
                "on the production server only"
            )
        if reader.problems:
            raise ConfigError(reader.problems)
        return settings


class _Reader:
    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = env
        self.problems: list[str] = []

    def _raw(self, key: str) -> str | None:
        value = self.env.get(key, "").strip()
        return value or None

    def text(self, key: str, default: str | None = None) -> str:
        value = self._raw(key)
        if value is None:
            if default is None:
                self.problems.append(f"{key} is required")
                return ""
            return default
        return value

    def integer(self, key: str, default: int, low: int, high: int) -> int:
        value = self._raw(key)
        if value is None:
            return default
        try:
            number = int(value)
        except ValueError:
            self.problems.append(f"{key} must be a whole number")
            return default
        if not low <= number <= high:
            self.problems.append(f"{key} must be between {low} and {high}")
            return default
        return number

    def boolean(self, key: str, default: bool) -> bool:
        value = self._raw(key)
        if value is None:
            return default
        if value.lower() in ("true", "1", "yes"):
            return True
        if value.lower() in ("false", "0", "no"):
            return False
        self.problems.append(f"{key} must be true or false")
        return default

    def choice[T: str](
        self, key: str, options: tuple[T, ...], default: T, upper: bool = False
    ) -> T:
        value = self._raw(key)
        if value is None:
            return default
        if upper:
            value = value.upper()
        for option in options:
            if value == option:
                return option
        self.problems.append(f"{key} must be one of: {', '.join(options)}")
        return default

    def optional_path(self, key: str, default: str) -> Path | None:
        """An empty value in the environment turns the feature off."""
        if key in self.env and not self.env[key].strip():
            return None
        return Path(self._raw(key) or default)

    def prefixes(self, key: str, default: str) -> tuple[str, ...]:
        items = tuple(p.strip() for p in (self._raw(key) or default).split(",") if p.strip())
        bad = [p for p in items if not valid_prefix(p)]
        if bad or not items:
            self.problems.append(f"{key} must be a comma-separated list of prefixes")
        return items
