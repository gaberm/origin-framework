from base import Record, Geometry, SHAPE_TYPE, Timestamp
from dataclasses import dataclass


@dataclass(kw_only=True)
class EV(Record, Geometry, Timestamp):
    table_name = "ev"
    primary_key = ("veh_id", "time")
    indexed = ("h3_ids", "state")

    veh_id: str
    tour_id: int
    soc: float
    state: str
    shape: SHAPE_TYPE = "POINT"


@dataclass(kw_only=True)
class VehicleBattery(Record):
    table_name = "vehicle_battery"
    primary_key = ("veh_id",)
    indexed = ("veh_id",)

    veh_id: str
    capacity: float
    charging_power: float
