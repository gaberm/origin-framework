from base import Input, Fields, Latest
from nashville.outputs.charging import ChargingEvent

vehicle_soc = Input(
    name="vehicle_soc",
    from_=ChargingEvent,
    select=Fields("veh_id", "final_soc"),
    read_policy=Latest(by="veh_id"),
)
