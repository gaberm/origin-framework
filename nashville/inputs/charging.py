from base import Input, Filter, Equal, Fields, KeyJoin, SpatialJoin
from nashville.outputs.sumo import EV, VehicleBattery
from nashville.outputs.charging import Station

arrived_vehicles = Input(
    name="arrived_vehicles",
    from_=EV,
    where=Filter(EV, "state", Equal("arrived")),
    on=[
        KeyJoin((EV, "veh_id"), (VehicleBattery, "veh_id")),
        SpatialJoin((EV, "coords"), (Station, "coords"), predicate="nearest"),
    ],
    select=Fields(
        (EV, "veh_id", "soc"),
        (VehicleBattery, "capacity", "charging_power"),
        (Station, "station_id"),
    ),
)

departed_vehicles = Input(
    name="departed_vehicles",
    from_=EV,
    where=Filter("state", Equal("departed")),
    select=Fields("veh_id", "soc"),
)
