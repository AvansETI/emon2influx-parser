from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from emon2influx.tools.fix_orientation import apply as apply_mod
from emon2influx.tools.fix_orientation.plan import Plan, make_plan
from emon2influx.tools.fix_orientation.store import (
    Correction,
    EnergyPoint,
    day_start,
    local_day,
)
from emon2influx.tools.fix_orientation.votes import DayStats, Vote, segments, vote_days

SIG = "2019-ETI-EMON-V01-A1B2C3-000000"
START = date(2025, 1, 1)


def day(i: int) -> date:
    return START + timedelta(days=i)


def stats(i: int, *, swapped: bool = False, points: int = 2880, use: float = 10.0) -> DayStats:
    export = 1.0
    consumed, produced = (export, use) if swapped else (use, export)
    return DayStats(day(i), points, use, consumed, produced)


class TestVotes:
    def test_votes(self) -> None:
        votes = vote_days(
            [stats(0), stats(1, swapped=True), stats(2, points=100), stats(3, use=0.2)]
        )
        assert [v.vote for v in votes] == [Vote.AS_NAMED, Vote.SWAPPED, Vote.UNCLEAR, Vote.UNCLEAR]

    def test_counters_equally_close_is_unclear(self) -> None:
        [vote] = vote_days([DayStats(day(0), 2880, 5.0, 5.1, 4.95)])
        assert vote.vote is Vote.UNCLEAR

    def test_swapped_run_is_corrected_and_noise_absorbed(self) -> None:
        days = [stats(i) for i in range(5)]
        days += [stats(i, swapped=True) for i in range(5, 20)]
        days[12] = stats(12)  # one odd day inside the swapped run
        days += [stats(i) for i in range(20, 25)]
        result = segments(vote_days(days))
        assert [(s.orientation, s.first, s.last, s.correct) for s in result] == [
            (Vote.AS_NAMED, day(0), day(4), False),
            (Vote.SWAPPED, day(5), day(19), True),
            (Vote.AS_NAMED, day(20), day(24), False),
        ]
        assert result[1].agreement == pytest.approx(14 / 15)

    def test_gaps_are_filled_only_between_agreeing_neighbours(self) -> None:
        days = [stats(i, swapped=True) for i in range(5)]
        days += [stats(i, points=10) for i in range(5, 8)]  # unclear gap
        days += [stats(i, swapped=True) for i in range(8, 12)]
        days += [stats(i, points=10) for i in range(12, 14)]
        days += [stats(i) for i in range(14, 20)]
        result = segments(vote_days(days))
        assert (result[0].orientation, result[0].first, result[0].last) == (
            Vote.SWAPPED,
            day(0),
            day(11),
        )
        assert result[1].orientation is Vote.UNCLEAR  # swapped → as-named inside a gap
        assert (result[1].first, result[1].last) == (day(12), day(13))

    def test_short_swapped_run_is_only_reported(self) -> None:
        days = [stats(i) for i in range(5)] + [stats(5, swapped=True), stats(6, swapped=True)]
        result = segments(vote_days(days))
        assert result[-1].orientation is Vote.SWAPPED and not result[-1].correct


class FakeStore:
    """Hourly points per day; counters swapped on the days given."""

    bucket = "test"
    measurement = "dsmr"

    def __init__(self, days: int, swapped_days: set[int]) -> None:
        self.points: dict[int, tuple[float, float]] = {}
        self.records: list[Correction] = []
        self.writes = 0
        consumed = produced = 1000.0
        for i in range(days):
            start = day_start(day(i))
            for h in range(24):
                consumed += 10 / 24
                produced += 1 / 24
                ns = int((start + timedelta(hours=h)).timestamp()) * 1_000_000_000
                self.points[ns] = (
                    (produced, consumed) if i in swapped_days else (consumed, produced)
                )

    def signatures(self, start: datetime, stop: datetime) -> list[str]:
        return [SIG]

    def _in(self, start: datetime, stop: datetime) -> list[int]:
        lo, hi = start.timestamp() * 1e9, stop.timestamp() * 1e9
        return sorted(ns for ns in self.points if lo <= ns < hi)

    def daily_stats(self, signature: str, start: datetime, stop: datetime) -> list[DayStats]:
        by_day: dict[date, list[int]] = {}
        for ns in self._in(start, stop):
            by_day.setdefault(local_day(datetime.fromtimestamp(ns / 1e9, UTC)), []).append(ns)
        result = []
        for d, stamps in sorted(by_day.items()):
            c = [self.points[ns][0] for ns in stamps]
            p = [self.points[ns][1] for ns in stamps]
            result.append(DayStats(d, len(stamps), 10 * 23 / 24, max(c) - min(c), max(p) - min(p)))
        return result

    def energy_points(
        self, signature: str, start: datetime, stop: datetime
    ) -> Iterator[EnergyPoint]:
        for ns in self._in(start, stop):
            yield EnergyPoint(ns, *self.points[ns])

    def raw_lines(
        self, signature: str, start: datetime, stop: datetime, fields: tuple[str, ...]
    ) -> Iterator[str]:
        return iter(())

    def corrections(self, signature: str) -> list[Correction]:
        return list(self.records)

    def write(self, lines: list[str]) -> None:
        self.writes += 1
        for line in lines:
            head, fields, stamp = line.split(" ")
            values = dict(f.split("=", 1) for f in fields.split(","))
            if head.startswith("dsmr_corrections"):
                self.records.append(
                    Correction(
                        SIG,
                        datetime.fromisoformat(values["from"].strip('"')),
                        datetime.fromisoformat(values["to"].strip('"')),
                        values["kind"].strip('"'),
                        datetime.fromtimestamp(int(stamp) / 1e9, UTC),
                    )
                )
            else:
                self.points[int(stamp)] = (
                    float(values["energy_consumed"]),
                    float(values["energy_produced"]),
                )


UNTIL = day_start(day(30))


def plan_for(store: FakeStore) -> Plan:
    return make_plan(store, [SIG], day_start(day(0)), UNTIL)


def test_plan_finds_the_swapped_period_with_dutch_day_bounds() -> None:
    plan = plan_for(FakeStore(40, set(range(10, 25))))
    [period] = plan.periods()
    assert (period.first_day, period.last_day) == ("2025-01-11", "2025-01-25")
    start, stop = period.bounds()
    assert start == datetime(2025, 1, 10, 23, tzinfo=UTC)  # 00:00 Dutch winter time
    assert stop == datetime(2025, 1, 25, 23, tzinfo=UTC)


def test_period_is_clipped_at_the_switch() -> None:
    plan = plan_for(FakeStore(40, set(range(20, 40))))
    [period] = plan.periods()
    assert period.bounds()[1] == UNTIL


def test_apply_swaps_once_records_and_verifies(tmp_path: Path) -> None:
    store = FakeStore(40, set(range(10, 25)))
    before = dict(store.points)
    [period] = plan_for(store).periods()
    result = apply_mod.apply_period(store, period, tmp_path, "dsmr")
    assert result.points == 15 * 24
    assert result.verified[Vote.AS_NAMED.value] == 15
    start, stop = period.bounds()
    for ns in store._in(start, stop):
        assert store.points[ns] == (before[ns][1], before[ns][0])
    assert [c.kind for c in store.records] == ["counters_swapped"]
    assert plan_for(store).periods() == []  # nothing left to correct


def test_running_again_after_an_interruption_does_not_swap_back(tmp_path: Path) -> None:
    store = FakeStore(40, set(range(10, 25)))
    [period] = plan_for(store).periods()
    apply_mod.apply_period(store, period, tmp_path, "dsmr")
    after = dict(store.points)
    # As if the record was never written: the same period is applied again from the backup.
    apply_mod.apply_period(store, period, tmp_path, "dsmr")
    assert store.points == after


def test_restore_brings_back_the_original_and_cancels_the_record(tmp_path: Path) -> None:
    store = FakeStore(40, set(range(10, 25)))
    before = dict(store.points)
    [period] = plan_for(store).periods()
    result = apply_mod.apply_period(store, period, tmp_path, "dsmr")
    restored, points = apply_mod.restore(store, result.backup)
    assert points == 15 * 24 and restored.signature == SIG
    assert store.points == before
    assert not apply_mod.already_corrected(store, period)
    assert len(plan_for(store).periods()) == 1


def test_swapped_line() -> None:
    line = f"dsmr,signature={SIG} energy_consumed=1.5,energy_produced=2.25 123"
    assert apply_mod.swapped(line) == (
        f"dsmr,signature={SIG} energy_consumed=2.25,energy_produced=1.5 123"
    )


def test_retry_then_resume_message() -> None:
    class Failing(FakeStore):
        def write(self, lines: list[str]) -> None:
            raise ConnectionError("down")

    store = Failing(1, set())
    with pytest.raises(RuntimeError, match="run again to resume"):
        apply_mod.write_lines(store, iter(["x"]), sleep=lambda s: None)


def test_cli_refuses_production_without_flag(tmp_path: Path) -> None:
    from emon2influx.tools.fix_orientation.__main__ import main

    env = tmp_path / "env"
    env.write_text(
        "INFLUX_URL=http://127.0.0.1:9\nINFLUX_ORG=o\nINFLUX_BUCKET=p1-smartmeters\n"
        "INFLUX_TOKEN=t\n"
    )
    with pytest.raises(SystemExit, match="--allow-production"):
        main(["restore", "--env", str(env), "--backup", str(tmp_path / "x.lp.gz"), "--yes"])
    with pytest.raises(SystemExit, match="--yes"):
        main(["restore", "--env", str(env), "--backup", str(tmp_path / "x.lp.gz")])
