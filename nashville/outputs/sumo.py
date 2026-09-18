from base import Record, Geometry, SHAPE_TYPE, Timestamp
from dataclasses import dataclass


@dataclass(kw_only=True)
class EV(Record, Geometry, Timestamp):
    table_name = "ev"
    primary_key = ("veh_id", "time", "state")
    indexed = ("h3_ids", "state")

    veh_id: str
    tour_id: int
    driver_id: str
    soc: float
    state: str
    fuel_type: str
    veh_class: str
    shape: SHAPE_TYPE = "POINT"


@dataclass(kw_only=True)
class VehicleBattery(Record):
    table_name = "vehicle_battery"
    primary_key = ("veh_id",)

    veh_id: str
    capacity_wh: float
    charging_power_w: float
