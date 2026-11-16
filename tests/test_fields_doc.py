from emon2influx.telegram.registry import ELECTRICITY, field_map
from emon2influx.tools.fields import main


def test_field_docs_are_up_to_date() -> None:
    assert main(["--check"]) == 0, "run: python -m emon2influx.tools.fields"


def test_every_field_names_its_obis_origin() -> None:
    for row in field_map():
        assert row["obis"], row["field"]
        assert row["description"], row["field"]


def test_phase_codes() -> None:
    codes = {spec.field: spec.obis for spec in ELECTRICITY}
    assert codes["voltage_l3"] == "1-0:72.7.0"
    assert codes["current_l2"] == "1-0:51.7.0"
    assert codes["power_produced_l3"] == "1-0:62.7.0"
