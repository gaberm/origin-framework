from dataclasses import dataclass
from base import SHAPE_TYPE, Entity, Geometry, Observation, ref
from nashville.outputs.sumo import EV, Trip


@dataclass(kw_only=True)
class Station(Entity, Geometry):
    id_field = "station_id"

    station_id: str
    source_id: str
    ac_ports: int
    dc_ports: int
    shape: SHAPE_TYPE = "POINT"


@dataclass(kw_only=True)
class Port(Entity):
    id_field = "port_id"

    port_id: str
    station_id: str = ref(Station)


@dataclass(kw_only=True)
class ChargingStep(Observation):
    diagnostic = True

    entity = Port

    driver_id: str = ref(EV)
    load_kw: float


@dataclass(kw_only=True)
class ChargingSession(Observation):
    entity = Port

    veh_id: str = ref(Trip)
    driver_id: str = ref(EV)
    final_charge_wh: float
    end_reason: str
