import pytest
from dataclasses import dataclass
from base import Record, Geometry, SHAPE_TYPE, Filter, Equal, Join, SpatialJoin, Fields, Input, Latest


@dataclass(kw_only=True)
class Vehicle(Record):
    table_name = "vehicle"
    primary_key = ("veh_id",)
    veh_id: str
    soc: float


@dataclass(kw_only=True)
class Battery(Record):
    table_name = "battery"
    primary_key = ("veh_id",)
    veh_id: str
    capacity: float


@dataclass(kw_only=True)
class VehicleLocation(Record, Geometry):
    table_name = "vehicle_location"
    primary_key = ("veh_id",)
    veh_id: str
    shape: SHAPE_TYPE = "POINT"


@dataclass(kw_only=True)
class Zone(Record, Geometry):
    table_name = "zone"
    primary_key = ("zone_id",)
    zone_id: str
    shape: SHAPE_TYPE = "POLYGON"


def test_wrong_filter_condition():
    with pytest.raises(TypeError, match="condition must be a Condition; got"):
        Filter("field", "bad_condition")


def test_valid_filter():
    filter_ = Filter(Vehicle, "veh_id", Equal("v1"))
    assert filter_.field == "veh_id"
    assert filter_.condition == Equal("v1")


def test_join_same_record():
    with pytest.raises(TypeError, match="left and right records must be different"):
        Join((Vehicle, "veh_id"), (Vehicle, "veh_id"))


def test_join_nonexistent_left_field():
    with pytest.raises(AttributeError, match="no field 'typo'"):
        Join((Vehicle, "typo"), (Battery, "veh_id"))


def test_join_nonexistent_right_field():
    with pytest.raises(AttributeError, match="no field 'typo'"):
        Join((Vehicle, "veh_id"), (Battery, "typo"))


def test_valid_join():
    join = Join((Vehicle, "veh_id"), (Battery, "veh_id"))
    assert join.left_record is Vehicle
    assert join.right_record is Battery
    assert join.left_field == "veh_id"
    assert join.right_field == "veh_id"


def test_spatial_join_same_record():
    with pytest.raises(TypeError, match="left and right records must be different"):
        SpatialJoin((VehicleLocation, "coords"), (VehicleLocation, "coords"))


def test_spatial_join_non_geometry_record():
    with pytest.raises(TypeError, match="requires Geometry records"):
        SpatialJoin((Vehicle, "veh_id"), (Zone, "coords"))


def test_spatial_join_invalid_mode():
    with pytest.raises(ValueError, match="mode must be one of"):
        SpatialJoin((VehicleLocation, "coords"), (Zone, "coords"), mode="bad_mode")


def test_spatial_join_invalid_duplicates():
    with pytest.raises(ValueError, match="duplicates must be 'keep' or 'drop'"):
        SpatialJoin((VehicleLocation, "coords"), (Zone, "coords"), duplicates="bad")


def test_valid_spatial_join():
    join = SpatialJoin((VehicleLocation, "coords"), (Zone, "coords"), mode="left", duplicates="drop")
    assert join.left_record is VehicleLocation
    assert join.right_record is Zone
    assert join.left_field == "coords"
    assert join.right_field == "coords"
    assert join.mode == "left"
    assert join.duplicates == "drop"


def test_empty_fields():
    with pytest.raises(TypeError, match="requires at least one field name"):
        Fields()


def test_inferred_fields():
    fields = Fields("veh_id", "soc")
    assert fields.selected_fields == {None: ("veh_id", "soc")}


def test_inferred_fields_non_string():
    with pytest.raises(TypeError, match="expected string field name"):
        Fields("veh_id", 123)


def test_explicit_fields():
    fields = Fields((Vehicle, "veh_id", "soc"))
    assert fields.selected_fields == {"vehicle": ("veh_id", "soc")}


def test_explicit_fields_nonexistent_field():
    with pytest.raises(AttributeError, match="no field 'typo'"):
        Fields((Vehicle, "veh_id", "typo"))


def test_explicit_fields_no_field_names():
    with pytest.raises(TypeError, match="no field names given for Vehicle"):
        Fields((Vehicle,))


def test_explicit_fields_multi_entity():
    fields = Fields((Vehicle, "veh_id", "soc"), (Battery, "capacity"))
    assert fields.selected_fields == {
        "vehicle": ("veh_id", "soc"),
        "battery": ("capacity",),
    }


def test_from_non_record_raises():
    with pytest.raises(TypeError, match="must be a Record subclass"):
        Input(name="BadInput", from_=str, select=Fields("veh_id"))


def test_missing_select_raises():
    with pytest.raises(TypeError, match="select must be Fields"):
        Input(name="BadInput", from_=Vehicle, select=None)


def test_select_nonexistent_field_raises():
    with pytest.raises(AttributeError, match="no field 'typo'"):
        Input(name="BadInput", from_=Vehicle, select=Fields("typo"))


def test_where_non_filter_raises():
    with pytest.raises(TypeError, match="must be .list of. Filter"):
        Input(name="BadInput", from_=Vehicle, where=["not_a_filter"], select=Fields("veh_id"))


def test_where_nonexistent_field_raises():
    with pytest.raises(AttributeError, match="no field 'typo'"):
        Input(name="BadInput", from_=Vehicle, where=[Filter("typo", Equal("v1"))], select=Fields("veh_id"))


def test_on_non_join_raises():
    with pytest.raises(TypeError, match="must be .list of. Join"):
        Input(name="BadInput", from_=Vehicle, on=["not_a_join"], select=Fields("veh_id"))


def test_invalid_read_policy_raises():
    with pytest.raises(TypeError, match="read_policy must be"):
        Input(name="BadInput", from_=Vehicle, select=Fields("veh_id"), read_policy="window")


def test_name_is_set():
    vehicle_input = Input(name="VehicleInput", from_=Vehicle, select=Fields("veh_id"))
    assert vehicle_input.name == "VehicleInput"


def test_select_resolves_table_name():
    vehicle_input = Input(name="VehicleInput", from_=Vehicle, select=Fields("veh_id", "soc"))
    assert "vehicle" in vehicle_input.select.selected_fields


def test_valid_simple_input():
    Input(name="VehicleInput", from_=Vehicle, select=Fields("veh_id", "soc"))


def test_valid_join_input():
    Input(
        name="VehicleInput",
        from_=Vehicle,
        on=Join((Vehicle, "veh_id"), (Battery, "veh_id")),
        select=Fields((Vehicle, "veh_id"), (Battery, "capacity")),
    )


def test_valid_latest_input():
    Input(
        name="VehicleInput",
        from_=Vehicle,
        select=Fields("veh_id", "soc"),
        read_policy=Latest(by="veh_id"),
    )
