import pytest
from dataclasses import dataclass
from base import Record, Timestamp
from nashville.outputs.charging import ChargingEvent, PortStatus, Station
from nashville.outputs.sumo import ArrivedEV, DepartedEV, VehicleBattery


def test_missing_primary_key_field():
    with pytest.raises(
        ValueError, match="BadRecord: primary_key field 'column_1' is not declared"
    ):

        @dataclass(kw_only=True)
        class BadRecord(Record):
            table_name = "bad_record"
            primary_key = ("column_1",)


def test_missing_indexed_field():
    with pytest.raises(
        ValueError, match="BadRecord: indexed field 'column_2' is not declared"
    ):

        @dataclass(kw_only=True)
        class BadRecord(Record):
            table_name = "bad_record"
            primary_key = ("column_1",)
            indexed = "column_2"

            column_1: str


def test_invalid_on_conflict_type():
    with pytest.raises(
        ValueError,
        match=".on_conflict must be 'IGNORE' or 'UPDATE'; got",
    ):

        @dataclass(kw_only=True)
        class BadRecord(Record):
            table_name = "bad_record"
            primary_key = ("column_1",)
            indexed = ()
            on_conflict = "bad_value"

            column_1: str


def test_invalid_diagnostic_type():
    with pytest.raises(
        TypeError,
        match=".diagnostic must be a boolean; got",
    ):

        @dataclass(kw_only=True)
        class BadRecord(Record):
            table_name = "bad_record"
            primary_key = ("column_1",)
            indexed = ()
            diagnostic = "bad_value"

            column_1: str


def test_valid_record():

    @dataclass(kw_only=True)
    class ValidRecord(Record):
        table_name = "valid_record"
        primary_key = ("column_1",)
        indexed = ()
        diagnostic = False

        column_1: str

    assert ValidRecord.primary_key == ("column_1",)
    ValidRecord(column_1="v1")


class TestTimeFieldIndexed:
    def test_added_for_timestamped_records(self):
        @dataclass(kw_only=True)
        class Reading(Record, Timestamp):
            table_name = "reading"
            primary_key = ("sensor_id", "time_s")
            indexed = ("value",)
            sensor_id: str
            value: float

        assert Reading.indexed == ("time_s", "value")

    def test_string_indexed_and_no_duplicate(self):
        @dataclass(kw_only=True)
        class Reading(Timestamp, Record):
            table_name = "reading"
            primary_key = "sensor_id"
            indexed = "time_s"
            sensor_id: str

        assert Reading.indexed == ("time_s",)

    def test_subclass_keeps_time_index(self):
        @dataclass(kw_only=True)
        class Reading(Record, Timestamp):
            table_name = "reading"
            primary_key = ("sensor_id",)
            sensor_id: str

        @dataclass(kw_only=True)
        class LabelledReading(Reading):
            indexed = ["label"]
            label: str

        assert Reading.indexed == ("time_s",)
        assert LabelledReading.indexed == ("time_s", "label")

    def test_not_added_without_timestamp(self):
        @dataclass(kw_only=True)
        class Sensor(Record):
            table_name = "sensor"
            primary_key = ("sensor_id",)
            indexed = ["kind"]
            sensor_id: str
            kind: str

        assert Sensor.indexed == ("kind",)

    def test_nashville_records(self):
        assert ArrivedEV.indexed == ("time_s", "h3_ids")
        assert DepartedEV.indexed == ("time_s",)
        assert PortStatus.indexed == ("time_s", "port_id", "station_id")
        assert ChargingEvent.indexed == ("time_s", "driver_id", "station_id")
        assert Station.indexed == ("h3_ids",)
        assert VehicleBattery.indexed == ()
