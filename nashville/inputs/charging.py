from base import Input, Filter, Equal, Fields, Join
from nashville.outputs.charging import Station
from nashville.outputs.sumo import EV, VehicleBattery


class ArrivedVehicles(Input):
    from_ = EV
    where = Filter(EV, "state", Equal("arrived"))
    on = Join((EV, "veh_id"), (VehicleBattery, "veh_id"))
    select = Fields(
        (EV, "veh_id", "soc"), (VehicleBattery, "capacity", "charging_power")
    )


class DepartedVehicles(Input):
    from_ = EV
    where = Filter("state", Equal("departed"))
    select = Fields("veh_id", "soc")
