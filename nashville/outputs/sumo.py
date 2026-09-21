from base import Record, Geometry, SHAPE_TYPE, Timestamp
from dataclasses import dataclass


@dataclass(kw_only=True)
class ArrivedEV(Record, Geometry, Timestamp):
    table_name = "arrived_ev"
    primary_key = ("veh_id",)
    indexed = ("h3_ids",)

    veh_id: str
    driver_id: str
    charge_wh: float
    veh_type: str
    shape: SHAPE_TYPE = "POINT"


@dataclass(kw_only=True)
class DepartedEV(Record, Timestamp):
    table_name = "departed_ev"
    primary_key = ("veh_id",)

    veh_id: str
    driver_id: str
    charge_wh: float


@dataclass(kw_only=True)
class VehicleBattery(Record):
    table_name = "vehicle_battery"
    primary_key = ("veh_id",)

    veh_id: str
    capacity_wh: float
    charging_power_w: float
