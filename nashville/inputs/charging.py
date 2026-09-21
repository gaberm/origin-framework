from base import Input, Filter, Equal, NotEqual, Fields, KeyJoin, SpatialJoin
from nashville.outputs.sumo import ArrivedEV, DepartedEV, VehicleBattery
from nashville.outputs.charging import Station

_PHEV_TYPE = "PHEV_car_std"


def _arrived(
    name: str, veh_type_filter: Filter, station_filters: list[Filter] = None
) -> Input:
    return Input(
        name=name,
        from_=ArrivedEV,
        where=[veh_type_filter],
        on=[
            KeyJoin((ArrivedEV, "veh_id"), (VehicleBattery, "veh_id")),
            SpatialJoin(
                (ArrivedEV, "coords"),
                (Station, "coords"),
                predicate="nearest",
                max_distance=400,
                where=station_filters,
            ),
        ],
        select=Fields(
            (ArrivedEV, "veh_id", "driver_id", "charge_wh", "time_s"),
            (VehicleBattery, "capacity_wh", "charging_power_w"),
            (Station, "station_id"),
        ),
    )


arrived_bevs = _arrived(
    "arrived_bevs", Filter(ArrivedEV, "veh_type", NotEqual(_PHEV_TYPE))
)
# PHEVs can't use DC fast chargers, so they only go to stations without any.
arrived_phevs = _arrived(
    "arrived_phevs",
    Filter(ArrivedEV, "veh_type", Equal(_PHEV_TYPE)),
    [Filter(Station, "dc_ports", Equal(0))],
)

departed_vehicles = Input(
    name="departed_vehicles",
    from_=DepartedEV,
    select=Fields("veh_id", "driver_id", "charge_wh", "time_s"),
)
