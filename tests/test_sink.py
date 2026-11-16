from pathlib import Path

from emon2influx.diagnostics import ErrorReporter
from emon2influx.sink.influx import InfluxSink, WriteError
from emon2influx.sink.spool import DiskSpool


class FakeInflux:
    """Up or down on command; records what it accepted."""

    def __init__(self) -> None:
        self.up = True
        self.reject: int | None = None
        self.accepted: list[str] = []
        self.calls = 0

    def write(self, lines: list[str]) -> None:
        self.calls += 1
        if self.reject:
            raise WriteError(f"answered {self.reject}", retryable=False, status=self.reject)
        if not self.up:
            raise WriteError("unreachable", retryable=True)
        self.accepted.extend(lines)

    def close(self) -> None:
        pass


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(tmp_path: Path, max_bytes: int = 10_000_000) -> tuple[InfluxSink, FakeInflux, Clock]:
    influx, clock = FakeInflux(), Clock()
    reporter = ErrorReporter(quarantine_file=tmp_path / "q.jsonl", limit_per_hour=5)
    sink = InfluxSink(
        influx,
        DiskSpool(tmp_path / "spool", max_bytes),
        reporter,
        batch_size=3,
        flush_interval_s=1,
        clock=clock,
    )
    return sink, influx, clock


def lines(start: int, count: int) -> list[str]:
    return [f"dsmr,signature=S value={i} {i}" for i in range(start, start + count)]


def test_writes_in_batches(tmp_path: Path) -> None:
    sink, influx, _ = make(tmp_path)
    sink.add(lines(0, 7))
    sink.tick()
    assert influx.accepted == lines(0, 7)
    assert influx.calls == 3


def test_outage_spools_and_replays_in_order(tmp_path: Path) -> None:
    sink, influx, clock = make(tmp_path)
    influx.up = False
    sink.add(lines(0, 4))
    sink.tick()
    assert sink.spool.count_files() == 2
    assert sink.outage_since is not None

    # Still down, and new data arrives: it goes behind the spooled data.
    clock.now = 10
    sink.add(lines(4, 2))
    sink.tick()
    assert influx.accepted == []

    influx.up = True
    clock.now = 1000
    sink.add(lines(6, 1))
    sink.tick()
    assert influx.accepted == lines(0, 7)
    assert sink.spool.is_empty()
    assert sink.outage_since is None


def test_spool_survives_a_restart(tmp_path: Path) -> None:
    sink, influx, _ = make(tmp_path)
    influx.up = False
    sink.add(lines(0, 3))
    sink.close()

    restarted, influx2, _ = make(tmp_path)
    restarted.tick()
    assert influx2.accepted == lines(0, 3)
    assert restarted.spool.is_empty()


def test_backoff_limits_attempts(tmp_path: Path) -> None:
    sink, influx, clock = make(tmp_path)
    influx.up = False
    sink.add(lines(0, 3))
    sink.tick()
    calls = influx.calls
    for _ in range(5):  # within the 1 s backoff: no new attempts
        sink.tick()
    assert influx.calls == calls
    clock.now = 2
    sink.tick()
    assert influx.calls == calls + 1


def test_rejected_data_is_quarantined_not_retried(tmp_path: Path) -> None:
    sink, influx, _ = make(tmp_path)
    influx.reject = 400
    sink.add(lines(0, 3))
    sink.tick()
    assert sink.spool.is_empty()
    assert sink.stats["rejected"] == 3
    assert "influx_rejected" in (tmp_path / "q.jsonl").read_text()


def test_full_spool_drops_the_oldest(tmp_path: Path) -> None:
    sink, influx, _ = make(tmp_path, max_bytes=200)
    influx.up = False
    for i in range(10):
        sink.add(lines(i * 3, 3))
        sink.tick()
    assert sink.stats["dropped"] > 0
    assert sink.spool.size_bytes <= 200 or sink.spool.count_files() == 1
    newest = sink.spool.read(sorted((tmp_path / "spool").glob("*.lp"))[-1])
    assert newest == lines(27, 3)
