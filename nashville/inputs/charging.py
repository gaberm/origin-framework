from base import Input, Filter, Equal, Fields, KeyJoin, SpatialJoin
from nashville.outputs.sumo import EV, VehicleBattery
from nashville.outputs.charging import Station


def _arrived(name: str, fuel_type: str, station_filters: list[Filter] = None) -> Input:
    return Input(
        name=name,
        from_=EV,
        where=[
            Filter(EV, "state", Equal("arrived")),
            Filter(EV, "veh_class", Equal("passenger")),
            Filter(EV, "fuel_type", Equal(fuel_type)),
        ],
        on=[
            KeyJoin((EV, "veh_id"), (VehicleBattery, "veh_id")),
            # Only cars parked within walking distance (metres) of a charger charge.
            SpatialJoin(
                (EV, "coords"),
                (Station, "coords"),
                predicate="nearest",
                max_distance=400,
                where=station_filters,
            ),
        ],
        select=Fields(
            (EV, "veh_id", "tour_id", "driver_id", "soc", "time_s"),
            (VehicleBattery, "capacity_wh", "charging_power_w"),
            (Station, "station_id"),
        ),
    )


arrived_bevs = _arrived("arrived_bevs", "EV")
arrived_phevs = _arrived(
    "arrived_phevs", "PHEV", [Filter(Station, "dc_ports", Equal(0))]
)

departed_vehicles = Input(
    name="departed_vehicles",
    from_=EV,
    where=[
        Filter("state", Equal("departed")),
        Filter("veh_class", Equal("passenger")),
    ],
    select=Fields("veh_id", "tour_id", "driver_id", "soc", "time_s"),
)
