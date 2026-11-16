from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from emon2influx.tools.drop_zero_gas.core import (
    GasRewriter,
    gas_values,
    make_plan,
    without_gas,
)
from emon2influx.tools.fix_orientation.store import day_start

NO_GAS = "2019-ETI-EMON-V01-A1B2C3-000000"
GAS = "2019-ETI-EMON-V01-FFFFFF-000000"
SINCE = day_start(date(2025, 3, 1))
UNTIL = day_start(date(2025, 3, 5))


def ns(moment: datetime) -> int:
    return int(moment.timestamp()) * 1_000_000_000


class FakeStore:
    """Points as line protocol per (signature, ns). Deleting works per signature and time,
    like InfluxDB 2.7, which refuses to delete a single field."""

    bucket = "test"

    def __init__(self) -> None:
        self.lines: dict[tuple[str, int], str] = {}
        self.records: list[str] = []
        self.fail_write_after: int | None = None
        for sig, gas in ((NO_GAS, 0.0), (GAS, 812.5)):
            for h in range(0, 6 * 24, 6):  # every 6 h for 6 days
                t = ns(SINCE + timedelta(hours=h))
                self.lines[(sig, t)] = (
                    f"dsmr,signature={sig} power_consumed=0.5,"
                    f'energy_consumed=1{h}.0,gas_delivered={gas},meter="KFM" {t}'
                )

    def _range(self, sig: str, start: datetime, stop: datetime) -> list[tuple[str, int]]:
        return sorted(k for k in self.lines if k[0] == sig and ns(start) <= k[1] < ns(stop))

    def field_summary(
        self, field: str, start: datetime, stop: datetime
    ) -> dict[str, tuple[int, float]]:
        result: dict[str, tuple[int, float]] = {}
        for (sig, _), line in self.lines.items():
            values = gas_values([line])
            if values:
                count, top = result.get(sig, (0, float("-inf")))
                result[sig] = (count + 1, max(top, *values))
        return result

    def point_lines(self, signature: str, start: datetime, stop: datetime) -> list[str]:
        return [self.lines[k] for k in self._range(signature, start, stop)]

    def delete_points(self, signature: str, start: datetime, stop: datetime) -> None:
        for key in self._range(signature, start, stop):
            del self.lines[key]

    def write(self, lines: list[str]) -> None:
        if self.fail_write_after is not None:
            if self.fail_write_after == 0:
                raise ConnectionError("down")
            self.fail_write_after -= 1
        for line in lines:
            if line.startswith("dsmr_corrections"):
                self.records.append(line)
                continue
            sig = line.split(",", 1)[1].split("=", 1)[1].split(" ", 1)[0]
            self.lines[(sig, int(line.rsplit(" ", 1)[1]))] = line


def test_line_without_gas_keeps_other_fields_and_types() -> None:
    line = 'dsmr,signature=S power_consumed=0.5,gas_delivered=0,meter="a,b",tariff=2i 1'
    assert without_gas(line) == 'dsmr,signature=S power_consumed=0.5,meter="a,b",tariff=2i 1'
    assert without_gas("dsmr,signature=S gas_delivered=0 1") is None


def test_plan_selects_only_households_with_gas_always_zero() -> None:
    plan = make_plan(FakeStore(), SINCE, UNTIL, datetime.now(UTC))
    assert list(plan.households) == [NO_GAS]


def test_rewrite_removes_gas_and_keeps_everything_else(tmp_path: Path) -> None:
    store = FakeStore()
    other_before = {k: v for k, v in store.lines.items() if k[0] == GAS}
    after_until = {k: v for k, v in store.lines.items() if k[0] == NO_GAS and k[1] >= ns(UNTIL)}
    days, removed = GasRewriter(store, tmp_path).rewrite(NO_GAS, SINCE, UNTIL)
    assert (days, removed) == (4, 16)
    rewritten = store.point_lines(NO_GAS, SINCE, UNTIL)
    assert len(rewritten) == 16 and not gas_values(rewritten)
    assert all("power_consumed=0.5" in line and 'meter="KFM"' in line for line in rewritten)
    assert {k: v for k, v in store.lines.items() if k[0] == GAS} == other_before
    assert {k: v for k, v in store.lines.items() if k[0] == NO_GAS and k[1] >= ns(UNTIL)} == (
        after_until
    )  # the new parser's period is never touched
    assert 'kind="gas_zero_removed"' in store.records[-1]


def test_interrupted_run_resumes_from_the_backup(tmp_path: Path) -> None:
    store = FakeStore()
    store.fail_write_after = 1  # the second day's write fails, after its delete
    with pytest.raises(ConnectionError):
        GasRewriter(store, tmp_path).rewrite(NO_GAS, SINCE, UNTIL)
    store.fail_write_after = None
    GasRewriter(store, tmp_path).rewrite(NO_GAS, SINCE, UNTIL)
    rewritten = store.point_lines(NO_GAS, SINCE, UNTIL)
    assert len(rewritten) == 16 and not gas_values(rewritten)  # nothing lost


def test_refuses_a_household_with_a_gas_meter(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="has a gas meter"):
        GasRewriter(FakeStore(), tmp_path).rewrite(GAS, SINCE, UNTIL)


def test_restore_brings_back_the_original(tmp_path: Path) -> None:
    store = FakeStore()
    before = dict(store.lines)
    rewriter = GasRewriter(store, tmp_path)
    rewriter.rewrite(NO_GAS, SINCE, UNTIL)
    [directory] = [d for d in tmp_path.iterdir() if d.is_dir()]
    sig, days = rewriter.restore(directory)
    assert (sig, days) == (NO_GAS, 4)
    assert store.lines == before
    assert 'kind="gas_zero_restored"' in store.records[-1]
