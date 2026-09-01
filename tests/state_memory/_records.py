from dataclasses import dataclass
from base import Record, Geometry, SHAPE_TYPE
from base.output.timestamp import Timestamp

RUN_ID = "run_test"


@dataclass(kw_only=True)
class Vehicle(Record, Timestamp):
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
class VehicleLocation(Record, Timestamp, Geometry):
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
