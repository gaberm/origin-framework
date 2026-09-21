from base import Input, Fields
from nashville.outputs.charging import ChargingEvent

vehicle_charge = Input(
    name="vehicle_charge",
    from_=ChargingEvent,
    select=Fields("driver_id", "final_charge_wh"),
)
