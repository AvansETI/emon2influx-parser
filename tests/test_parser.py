from datetime import UTC, datetime

import pytest

from emon2influx.errors import Category, RejectedMessage
from emon2influx.telegram.frame import TelegramFrame, crc16
from emon2influx.telegram.parser import TelegramParser
from emon2influx.telegram.timestamp import parse_meter_time

from .telegrams import (
    BELGIAN_LINES,
    DSMR22_LINES,
    DSMR42_LINES,
    DSMR50_LINES,
    EAN,
    NO_GAS_LINES,
    with_crc,
    without_crc,
)

RECEIVED = datetime(2026, 11, 16, 17, 10, 30, tzinfo=UTC)
parser = TelegramParser(require_valid_crc=True)


def test_crc16_known_value() -> None:
    assert crc16(b"123456789") == 0xBB3D  # CRC-16/ARC check value


class TestFrame:
    def test_rejects_a_wrong_crc(self) -> None:
        text = with_crc(DSMR50_LINES)
        broken = text.replace("01.193", "01.194")
        with pytest.raises(RejectedMessage) as error:
            TelegramFrame.parse(broken, require_valid_crc=True)
        assert error.value.category == Category.CRC
        assert TelegramFrame.parse(broken, require_valid_crc=False).crc is not None

    def test_accepts_old_telegrams_without_crc(self) -> None:
        frame = TelegramFrame.parse(without_crc(DSMR22_LINES), require_valid_crc=True)
        assert frame.crc is None
        assert frame.manufacturer == "ISk"

    @pytest.mark.parametrize("text", ["", "/6", "1-0:1.8.1(1*kWh)\r\n!", "/\r\n!"])
    def test_rejects_broken_frames(self, text: str) -> None:
        with pytest.raises(RejectedMessage) as error:
            TelegramFrame.parse(text, require_valid_crc=True)
        assert error.value.category == Category.TELEGRAM


class TestTime:
    def test_winter_and_summer_suffix(self) -> None:
        assert parse_meter_time("261116181022W") == datetime(2026, 11, 16, 17, 10, 22, tzinfo=UTC)
        assert parse_meter_time("260716181022S") == datetime(2026, 7, 16, 16, 10, 22, tzinfo=UTC)

    def test_without_suffix_uses_dutch_local_time(self) -> None:
        assert parse_meter_time("260716180000") == datetime(2026, 7, 16, 16, 0, tzinfo=UTC)
        assert parse_meter_time("261116180000") == datetime(2026, 11, 16, 17, 0, tzinfo=UTC)

    @pytest.mark.parametrize("text", ["", "261332000000W", "26111618", "2611161810xxW"])
    def test_invalid(self, text: str) -> None:
        assert parse_meter_time(text) is None


class TestDsmr50:
    reading = parser.parse(with_crc(DSMR50_LINES), RECEIVED)

    def test_counters_per_tariff_and_totals(self) -> None:
        e = self.reading.electricity
        assert e["energy_consumed_t1"] == 1234.567
        assert e["energy_consumed_t2"] == 2345.678
        assert e["energy_consumed"] == pytest.approx(3580.245)
        assert e["energy_produced"] == pytest.approx(300.003)

    def test_phases_and_quality(self) -> None:
        e = self.reading.electricity
        assert (e["voltage_l1"], e["voltage_l2"], e["voltage_l3"]) == (230.1, 231.2, 229.9)
        assert (e["current_l1"], e["current_l3"]) == (3.0, 0.0)
        assert e["power_consumed_l2"] == 0.4
        assert e["voltage_sags_l1"] == 2 and isinstance(e["voltage_sags_l1"], int)
        assert e["power_failures"] == 7
        assert e["tariff"] == 2

    def test_metadata_and_clock(self) -> None:
        e = self.reading.electricity
        assert e["meter_manufacturer"] == "CTA"
        assert e["dsmr_version"] == "50"
        assert e["clock_offset_s"] == 8  # received 17:10:30, meter 17:10:22 UTC

    def test_gas_on_its_capture_time(self) -> None:
        [gas] = self.reading.mbus
        assert gas.field == "gas_delivered"
        assert gas.value == 4567.891
        assert gas.captured_at == datetime(2026, 11, 16, 17, 10, tzinfo=UTC)

    def test_never_stores_identifiers(self) -> None:
        values = [str(v) for v in self.reading.electricity.values()]
        assert not any(EAN in v or "5465" in v for v in values)
        assert self.reading.issues == []
        assert self.reading.unknown_codes == []


def test_dsmr42_hourly_gas_keeps_its_hour() -> None:
    reading = parser.parse(with_crc(DSMR42_LINES), datetime(2026, 11, 16, 18, 10, 52, tzinfo=UTC))
    [gas] = reading.mbus
    assert gas.captured_at == datetime(2026, 11, 16, 18, 0, tzinfo=UTC)
    assert reading.electricity["energy_consumed"] == pytest.approx(33333.333)


class TestWithoutVersionLine:
    def test_counters_are_not_swapped(self) -> None:
        # The old parser wrote 2.8.x as energy_consumed for these meters.
        reading = parser.parse(without_crc(DSMR22_LINES), RECEIVED)
        assert reading.electricity["energy_consumed"] == pytest.approx(35802.467)
        assert reading.electricity["energy_produced"] == pytest.approx(8024.68)
        assert "dsmr_version" not in reading.electricity

    def test_multi_line_gas_reading(self) -> None:
        reading = parser.parse(without_crc(DSMR22_LINES), RECEIVED)
        [gas] = reading.mbus
        assert gas.value == 5432.109
        assert gas.captured_at == datetime(2026, 11, 16, 17, 0, tzinfo=UTC)
        assert reading.electricity["power_threshold"] == 999.0
        assert reading.electricity["breaker_state"] == 1

    def test_missing_values_are_absent_not_zero(self) -> None:
        reading = parser.parse(without_crc(NO_GAS_LINES), RECEIVED)
        assert reading.mbus == []
        assert "voltage_l1" not in reading.electricity
        assert "clock_offset_s" not in reading.electricity
        assert reading.electricity["energy_consumed"] == pytest.approx(9753.1)
        assert reading.electricity["energy_produced"] == 0.0


def test_belgian_meter() -> None:
    reading = parser.parse(with_crc(BELGIAN_LINES), RECEIVED)
    e = reading.electricity
    assert e["dsmr_version"] == "BE50217"
    assert e["meter_manufacturer"] == "FLU"
    assert e["demand_current"] == 2.351
    assert e["demand_max_month"] == 5.124
    assert e["current_l1"] == 2.3
    assert e["current_threshold"] == 999.0
    fields = {m.field: m.value for m in reading.mbus}
    assert fields == {"gas_delivered": 112.384, "water_delivered": 872.234}


class TestRobustness:
    def test_a_bad_line_skips_only_that_field(self) -> None:
        lines = [*DSMR50_LINES]
        lines[lines.index("1-0:32.7.0(230.1*V)")] = "1-0:32.7.0(23x.1*V)"
        reading = parser.parse(with_crc(lines), RECEIVED)
        assert "voltage_l1" not in reading.electricity
        assert reading.electricity["voltage_l2"] == 231.2
        assert [i.message for i in reading.issues] == ["voltage_l1: unreadable value '23x.1'"]

    def test_unexpected_unit_is_reported(self) -> None:
        lines = [*DSMR50_LINES]
        lines[lines.index("1-0:1.7.0(01.193*kW)")] = "1-0:1.7.0(01.193*kV)"
        reading = parser.parse(with_crc(lines), RECEIVED)
        assert "power_consumed" not in reading.electricity
        assert "unexpected unit 'kV'" in reading.issues[0].message

    def test_watts_are_converted_to_kilowatts(self) -> None:
        lines = [*DSMR50_LINES]
        lines[lines.index("1-0:1.7.0(01.193*kW)")] = "1-0:1.7.0(1193*W)"
        assert parser.parse(with_crc(lines), RECEIVED).electricity["power_consumed"] == 1.193

    def test_untrusted_gas_time_falls_back_to_receipt(self) -> None:
        lines = [*DSMR50_LINES]
        lines[-1] = "0-1:24.2.1(000101000001W)(04567.891*m3)"
        reading = parser.parse(with_crc(lines), RECEIVED)
        assert reading.mbus[0].captured_at == RECEIVED
        assert reading.issues[0].category == "mbus_time"

    def test_unknown_codes_are_collected(self) -> None:
        lines = [*DSMR50_LINES, "1-0:14.7.0(50.01*Hz)"]
        reading = parser.parse(with_crc(lines), RECEIVED)
        assert reading.unknown_codes == ["1-0:14.7.0"]
