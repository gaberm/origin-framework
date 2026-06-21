from dataclasses import dataclass
from base import Geometry, Record, SHAPE_TYPE, Timestamp


@dataclass(kw_only=True)
class Station(Record, Geometry):
    table_name = "stations"
    primary_key = ("station_id",)
    indexed = ("h3_ids",)

    station_id: str
    shape: SHAPE_TYPE = "POINT"


@dataclass(kw_only=True)
class PortStatus(Record, Timestamp):
    table_name = "port_status"
    primary_key = ("port_id", "station_id", "time")
    indexed = ("port_id", "station_id", "time")

    port_id: str
    station_id: str
    load: float


@dataclass(kw_only=True)
class ChargingEvent(Record, Timestamp):
    table_name = "charging_events"
    primary_key = ("veh_id", "time")
    indexed = ("veh_id", "station_id", "time")

    veh_id: str
    station_id: str
    port_id: str
    final_soc: float
