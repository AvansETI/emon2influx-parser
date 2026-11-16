import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emon2influx.config import Settings
from emon2influx.container import build
from emon2influx.diagnostics.rate_limit import Decision, RateLimiter
from emon2influx.logging_setup import setup_logging
from emon2influx.metrics import Metrics
from emon2influx.sink.writer import DryRunWriter
from emon2influx.summary import SummaryReporter
from emon2influx.telegram.derived import DERIVERS
from emon2influx.telegram.registry import DERIVED
from emon2influx.worker import Worker

from .telegrams import DSMR50_LINES, payload, with_crc


def test_every_derived_field_has_one_function() -> None:
    assert set(DERIVERS) == {spec.field for spec in DERIVED}


def test_rate_limiter_resets_each_hour() -> None:
    now = [datetime(2026, 11, 16, 10, 5, tzinfo=UTC)]
    limiter = RateLimiter(2, clock=lambda: now[0])
    assert [limiter.decide("kit", "crc") for _ in range(3)] == [
        Decision.FULL,
        Decision.LAST,
        Decision.COUNT,
    ]
    assert limiter.decide("other", "crc") is Decision.FULL
    now[0] += timedelta(hours=1)
    assert limiter.decide("kit", "crc") is Decision.FULL


def test_metrics_snapshot_and_reset() -> None:
    metrics = Metrics()
    metrics.incr("messages", 3)
    metrics.error("crc")
    metrics.kit("a")
    metrics.unknown_codes(["1-0:14.7.0"])
    snap = metrics.snapshot(reset=True)
    assert (snap.counters["messages"], snap.errors["crc"], snap.kits) == (3, 1, frozenset({"a"}))
    assert metrics.snapshot().counters == {}


def settings(tmp_path: Path, **extra: str) -> Settings:
    return Settings.from_env(
        {
            "MQTT_HOST": "broker.example",
            "LOG_DIR": str(tmp_path / "logs"),
            "SPOOL_DIR": str(tmp_path / "spool"),
            "QUARANTINE_FILE": str(tmp_path / "logs" / "q.jsonl"),
            **extra,
        }
    )


def test_errors_go_to_the_error_log_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logging(settings(tmp_path))
    logging.getLogger("emon2influx.errors").warning("an error record")
    logging.getLogger("emon2influx").info("an operational record")
    for handler in (
        logging.getLogger("emon2influx").handlers + logging.getLogger("emon2influx.errors").handlers
    ):
        handler.flush()
    main = (tmp_path / "logs" / "emon2influx-parser.log").read_text()
    errors = (tmp_path / "logs" / "errors.log").read_text()
    console = capsys.readouterr().err
    assert "an error record" in errors
    assert "an error record" not in main and "an error record" not in console
    assert "an operational record" in main and "an operational record" in console


def test_worker_runs_messages_through_to_the_sink(tmp_path: Path) -> None:
    c = build(settings(tmp_path, HEALTH_FILE=""), writer=DryRunWriter())
    received = datetime(2026, 11, 16, 17, 10, 30, tzinfo=UTC)
    c.messages.put((payload(with_crc(DSMR50_LINES)), received))
    c.messages.put((b"1", received))
    worker = Worker(c.messages, c.pipeline, c.sink, c.summary)
    assert worker.drain() == 2
    c.sink.tick()
    line = SummaryReporter(c.metrics, c.spool).report()
    assert "messages=2 kits=1 points=2 rejected=1 written=2" in line
    assert c.field_map is None  # dry-run never writes dsmr_fields


def test_health_file_reports_status(tmp_path: Path) -> None:
    import json

    c = build(settings(tmp_path, HEALTH_FILE=str(tmp_path / "health.json")), writer=DryRunWriter())
    assert c.health is not None
    c.messages.put((payload(with_crc(DSMR50_LINES)), datetime(2026, 11, 16, tzinfo=UTC)))
    Worker(c.messages, c.pipeline, c.sink, c.summary, c.health).drain()
    c.sink.tick()
    c.health.write()
    status = json.loads((tmp_path / "health.json").read_text())
    assert status["messages"] == 1
    assert status["mqtt_connected"] is False
    assert status["last_written_at"] is not None
    assert status["spool_files"] == 0
    assert not (tmp_path / "health.json.tmp").exists()


def test_totals_survive_a_summary() -> None:
    metrics = Metrics()
    metrics.incr("messages", 2)
    metrics.snapshot(reset=True)
    metrics.incr("messages")
    assert metrics.total("messages") == 3


def test_cli_check_config_and_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from emon2influx import __version__
    from emon2influx.__main__ import main

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip().startswith(__version__)
    env = tmp_path / "env"
    env.write_text("MQTT_HOST=broker.example\nMODE=write\n")
    for key in ("MQTT_HOST", "MODE", "INFLUX_URL", "INFLUX_ORG", "INFLUX_BUCKET", "INFLUX_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    assert main(["--check-config", "--env-file", str(env)]) == 2
    assert "INFLUX_TOKEN is required" in capsys.readouterr().err
    env.write_text("MQTT_HOST=broker.example\n")
    monkeypatch.delenv("MODE", raising=False)
    assert main(["--check-config", "--env-file", str(env)]) == 0
