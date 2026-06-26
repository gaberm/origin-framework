from base import Input, Filter, Equal, Fields, Join
from nashville.outputs.sumo import EV, VehicleBattery

arrived_vehicles = Input(
    name="arrived_vehicles",
    from_=EV,
    where=Filter(EV, "state", Equal("arrived")),
    on=Join((EV, "veh_id"), (VehicleBattery, "veh_id")),
    select=Fields(
        (EV, "veh_id", "soc"), (VehicleBattery, "capacity", "charging_power")
    ),
)

departed_vehicles = Input(
    name="departed_vehicles",
    from_=EV,
    where=Filter("state", Equal("departed")),
    select=Fields("veh_id", "soc"),
)
