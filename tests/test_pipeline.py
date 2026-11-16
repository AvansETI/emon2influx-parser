import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from emon2influx.diagnostics import ErrorReporter
from emon2influx.errors import Category, RejectedMessage
from emon2influx.payload.registry import DecoderRegistry
from emon2influx.pipeline import Pipeline
from emon2influx.points import PointBuilder
from emon2influx.signature import Signature
from emon2influx.telegram.parser import TelegramParser

from .telegrams import (
    DSMR50_LINES,
    EAN,
    NO_GAS_LINES,
    OTHER_SIGNATURE,
    SIGNATURE,
    payload,
    with_crc,
    without_crc,
)

RECEIVED = datetime(2026, 11, 16, 17, 10, 30, tzinfo=UTC)
PREFIXES = ("2019-ETI-EMON-V01",)


def make(tmp_path: Path) -> tuple[Pipeline, Path]:
    quarantine = tmp_path / "quarantine.jsonl"
    reporter = ErrorReporter(quarantine_file=quarantine, limit_per_hour=5)
    pipeline = Pipeline(DecoderRegistry(PREFIXES), TelegramParser(), PointBuilder("dsmr"), reporter)
    return pipeline, quarantine


class TestSignature:
    def test_valid(self) -> None:
        assert Signature.parse(SIGNATURE, PREFIXES).value == SIGNATURE

    @pytest.mark.parametrize(
        "raw",
        [
            "2019-ETI-EMON-V01-a1b2c3-000000",
            "2019-ETI-EMON-V01-A1B2C3-000000 extra=1",
            "2019-ETI-EMON-V01-A1B2C3-000000,signature=x",
            "2026-MNEXT-EMON-V02-A1B2C3-000000",
            "2019-ETI-EMON-V01-A1B2C3",
            "",
            42,
            None,
        ],
    )
    def test_rejected(self, raw: object) -> None:
        with pytest.raises(RejectedMessage) as error:
            Signature.parse(raw, PREFIXES)
        assert error.value.category == Category.SIGNATURE


class TestDecoders:
    registry = DecoderRegistry(PREFIXES)

    @pytest.mark.parametrize(
        ("raw", "category"),
        [
            (b"1", Category.PAYLOAD_SHAPE),
            (b"hello world", Category.PAYLOAD_JSON),
            (b'{"datagram":{"p1":"/\x12\x14BP1"}}', Category.PAYLOAD_JSON),
            (b"\xff\xfe", Category.PAYLOAD_JSON),
            (b'{"something":"else"}', Category.PAYLOAD_UNSUPPORTED),
            (b'{"datagram":{"signature":"x"}}', Category.PAYLOAD_SHAPE),
        ],
    )
    def test_rejects(self, raw: bytes, category: Category) -> None:
        with pytest.raises(RejectedMessage) as error:
            self.registry.decode(raw, RECEIVED)
        assert error.value.category == category

    def test_v02_prefix_without_decoder_is_unsupported(self) -> None:
        registry = DecoderRegistry((*PREFIXES, "2026-MNEXT-EMON-V02"))
        raw = payload("/X", signature="2026-MNEXT-EMON-V02-A1B2C3-000000")
        with pytest.raises(RejectedMessage) as error:
            registry.decode(raw, RECEIVED)
        assert error.value.category == Category.PAYLOAD_UNSUPPORTED


class TestPipeline:
    def test_writes_electricity_and_gas_points(self, tmp_path: Path) -> None:
        pipeline, _ = make(tmp_path)
        lines = pipeline.process(payload(with_crc(DSMR50_LINES)), RECEIVED)
        assert len(lines) == 2
        electricity, gas = lines
        assert electricity.startswith(f"dsmr,signature={SIGNATURE} ")
        assert "energy_consumed=3580.245" in electricity
        assert "voltage_sags_l1=2i" in electricity
        assert 'meter_manufacturer="CTA"' in electricity
        assert "gas_delivered" not in electricity
        assert gas == f"dsmr,signature={SIGNATURE} gas_delivered=4567.891 1794849000000000000"
        assert electricity.endswith(" 1794849030000000000")
        assert EAN not in electricity

    def test_absent_gas_is_not_written(self, tmp_path: Path) -> None:
        pipeline, _ = make(tmp_path)
        [line] = pipeline.process(payload(without_crc(NO_GAS_LINES), OTHER_SIGNATURE), RECEIVED)
        assert "gas_delivered" not in line
        assert "energy_produced=0," in line  # a float; the client writes 0.0 as 0

    def test_wrong_crc_is_written_with_a_flag(self, tmp_path: Path) -> None:
        pipeline, quarantine = make(tmp_path)
        broken = with_crc(DSMR50_LINES).replace("01.193", "01.194")
        electricity, _gas = pipeline.process(payload(broken), RECEIVED)
        assert "crc_valid=false" in electricity
        assert "power_consumed=1.194" in electricity
        assert pipeline.reporter.take_counts() == {"crc": 1}
        assert not quarantine.exists()

    def test_valid_crc_is_flagged_true_and_old_meters_have_no_flag(self, tmp_path: Path) -> None:
        pipeline, _ = make(tmp_path)
        assert "crc_valid=true" in pipeline.process(payload(with_crc(DSMR50_LINES)), RECEIVED)[0]
        [old] = pipeline.process(payload(without_crc(NO_GAS_LINES)), RECEIVED)
        assert "crc_valid" not in old

    def test_rejected_message_is_quarantined_without_identifiers(self, tmp_path: Path) -> None:
        pipeline, quarantine = make(tmp_path)
        truncated = with_crc(DSMR50_LINES).split("!")[0]
        assert pipeline.process(payload(truncated), RECEIVED) == []
        [record] = [json.loads(line) for line in quarantine.read_text().splitlines()]
        assert record["category"] == "telegram"
        assert record["signature"] == SIGNATURE
        assert EAN not in record["raw"]
        assert "0-0:96.1.1(…)" in record["raw"]
        assert pipeline.stats["rejected"] == 1

    def test_noise_is_not_quarantined(self, tmp_path: Path) -> None:
        pipeline, quarantine = make(tmp_path)
        assert pipeline.process(b"1", RECEIVED) == []
        assert not quarantine.exists() or quarantine.read_text() == ""

    def test_counts_kits_and_unknown_codes(self, tmp_path: Path) -> None:
        pipeline, _ = make(tmp_path)
        pipeline.process(payload(with_crc([*DSMR50_LINES, "1-0:14.7.0(50.01*Hz)"])), RECEIVED)
        pipeline.process(payload(without_crc(NO_GAS_LINES), OTHER_SIGNATURE), RECEIVED)
        assert pipeline.kits == {SIGNATURE, OTHER_SIGNATURE}
        assert pipeline.unknown_codes == {"1-0:14.7.0": 1}


def test_error_log_is_rate_limited(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    reporter = ErrorReporter(quarantine_file=None, limit_per_hour=2)
    with caplog.at_level("WARNING", logger="emon2influx.errors"):
        for _ in range(5):
            reporter.report(Category.CRC, "CRC mismatch", signature=SIGNATURE)
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 3  # two full records and the "limit reached" notice
    assert "limit reached" in messages[-1]
    assert reporter.take_counts() == {"crc": 5}
