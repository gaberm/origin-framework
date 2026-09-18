from base import Input, Fields
from nashville.outputs.charging import ChargingEvent

vehicle_soc = Input(
    name="vehicle_soc",
    from_=ChargingEvent,
    select=Fields("driver_id", "final_soc"),
)
