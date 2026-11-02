from base import (
    Input,
    Filter,
    Equal,
    NotEqual,
    Fields,
    KeyJoin,
    SpatialJoin,
)
from nashville.outputs.sumo import EV, Arrival, Departure, Trip, VehicleBattery
from nashville.outputs.charging import Station

_PHEV_TYPE = "PHEV_car_std"


def _arrived(
    name: str,
    vehicle_filters: list[Filter],
    station_filters: list[Filter],
) -> Input:
    return Input(
        name=name,
        from_=Arrival,
        where=vehicle_filters,
        on=[
            KeyJoin((Arrival, "veh_id"), (Trip, "veh_id")),
            KeyJoin(Trip, EV),
            KeyJoin((Arrival, "veh_id"), (VehicleBattery, "veh_id")),
            SpatialJoin(
                Arrival,
                Station,
                predicate="nearest",
                max_distance=400,
                where=station_filters,
            ),
        ],
        select=Fields(
            (Arrival, "veh_id", "charge_wh", "time_s"),
            (Trip, "driver_id"),
            (VehicleBattery, "capacity_wh", "charge_level_table", "charge_curve_table"),
            (Station, "station_id"),
        ),
    )


arrived_bevs = _arrived(
    "arrived_bevs",
    [Filter(EV, "veh_type", NotEqual(_PHEV_TYPE))],
    [],
)
# PHEVs can't use DC fast chargers, so they only go to stations without any.
arrived_phevs = _arrived(
    "arrived_phevs",
    [Filter(EV, "veh_type", Equal(_PHEV_TYPE))],
    [Filter(Station, "dc_ports", Equal(0))],
)

departed_vehicles = Input(
    name="departed_vehicles",
    from_=Departure,
    on=KeyJoin((Departure, "veh_id"), (Trip, "veh_id")),
    select=Fields((Departure, "veh_id", "time_s"), (Trip, "driver_id")),
)
