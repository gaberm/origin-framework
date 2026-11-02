from dataclasses import dataclass
from base import SHAPE_TYPE, Entity, Geometry, StartObservation, Observation, ref


@dataclass(kw_only=True)
class EV(Entity):
    id_field = "driver_id"

    driver_id: str
    veh_type: str


class Departure(StartObservation, Geometry):
    shape: SHAPE_TYPE = "POINT"


@dataclass(kw_only=True)
class Trip(Entity):
    id_field = "veh_id"
    start_observation = Departure

    veh_id: str
    driver_id: str = ref(EV)


@dataclass(kw_only=True)
class Arrival(Observation, Geometry):
    id_field = "veh_id"
    observation_type = "end"

    entity = Trip

    veh_id: str
    charge_wh: float
    shape: SHAPE_TYPE = "POINT"


@dataclass(kw_only=True)
class VehicleBattery(Entity):
    id_field = "veh_id"

    veh_id: str
    driver_id: str = ref(EV)
    capacity_wh: float
    charge_level_table: list[float]
    charge_curve_table: list[float]
