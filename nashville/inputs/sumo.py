from base import Input, Fields
from nashville.outputs.charging import ChargingSession

vehicle_charge = Input(
    name="vehicle_charge",
    from_=ChargingSession,
    select=Fields("driver_id", "final_charge_wh"),
)
