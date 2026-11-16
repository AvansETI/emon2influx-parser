from pathlib import Path

import pytest

from emon2influx.config import ConfigError, Settings

BASE = {"MQTT_HOST": "broker.example"}
WRITE = {
    **BASE,
    "MODE": "write",
    "INFLUX_URL": "https://influx.example",
    "INFLUX_ORG": "org",
    "INFLUX_BUCKET": "p1-smartmeters-test",
    "INFLUX_TOKEN": "secret-token",
}


def test_dry_run_is_the_default_and_needs_no_influx() -> None:
    settings = Settings.from_env(BASE)
    assert settings.mode == "dry-run"
    assert settings.signature_prefixes == ("2019-ETI-EMON-V01",)
    assert settings.log_dir == Path("logs")


def test_secrets_are_not_in_repr() -> None:
    settings = Settings.from_env({**WRITE, "MQTT_PASSWORD": "mqtt-secret"})
    assert "secret-token" not in repr(settings)
    assert "mqtt-secret" not in repr(settings)


def test_write_mode_requires_influx_settings() -> None:
    with pytest.raises(ConfigError) as error:
        Settings.from_env({**BASE, "MODE": "write"})
    assert "INFLUX_TOKEN is required" in error.value.problems


def test_production_bucket_needs_an_explicit_switch() -> None:
    production = {**WRITE, "INFLUX_BUCKET": "p1-smartmeters"}
    with pytest.raises(ConfigError):
        Settings.from_env(production)
    assert Settings.from_env({**production, "ALLOW_PRODUCTION_BUCKET": "true"}).mode == "write"


def test_all_problems_are_reported_at_once() -> None:
    with pytest.raises(ConfigError) as error:
        Settings.from_env({"MQTT_PORT": "x", "MODE": "fast", "SIGNATURE_PREFIXES": "a b"})
    assert len(error.value.problems) == 4


def test_empty_log_dir_turns_file_logging_off() -> None:
    assert Settings.from_env({**BASE, "LOG_DIR": "", "QUARANTINE_FILE": ""}).log_dir is None
