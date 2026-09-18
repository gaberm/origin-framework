from dataclasses import dataclass
from base import Geometry, Record, SHAPE_TYPE, Timestamp


@dataclass(kw_only=True)
class Station(Record, Geometry):
    table_name = "stations"
    primary_key = ("station_id",)
    indexed = ("h3_ids",)

    station_id: str
    ac_ports: int
    dc_ports: int
    shape: SHAPE_TYPE = "POINT"


@dataclass(kw_only=True)
class PortStatus(Record, Timestamp):
    table_name = "port_status"
    primary_key = (
        "port_id",
        "station_id",
    )
    indexed = ("port_id", "station_id")
    diagnostic = True

    port_id: str
    station_id: str
    driver_id: str
    load_kw: float


@dataclass(kw_only=True)
class ChargingEvent(Record, Timestamp):
    table_name = "charging_events"
    primary_key = "veh_id"
    indexed = ("driver_id", "tour_id", "station_id")

    veh_id: str
    tour_id: int
    driver_id: str
    station_id: str
    port_id: str
    final_soc: float
    end_reason: str
