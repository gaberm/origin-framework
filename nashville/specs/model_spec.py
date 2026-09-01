from pathlib import Path
from base import ModelSpec
from nashville.adapters.charging_adapter import ChargingAdapter
from nashville.adapters.sumo_adapter import SumoAdapter

_NASHVILLE_DIR = Path(__file__).resolve().parent.parent

sumo_spec = ModelSpec(
    name="sumo",
    adapter=SumoAdapter,
    timestep_length=1.0,
    params={
        "sumo_config": str(_NASHVILLE_DIR / "data" / "sumo" / "main.sumocfg"),
    },
)

charging_spec = ModelSpec(
    name="charging",
    adapter=ChargingAdapter,
    timestep_length=1.0,
)

MODEL_SPECS = [sumo_spec, charging_spec]
