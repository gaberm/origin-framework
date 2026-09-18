from datetime import timedelta
from pathlib import Path
from base import ModelSpec
from nashville.adapters.charging_adapter import ChargingAdapter
from nashville.adapters.sumo_adapter import SumoAdapter

_NASHVILLE_DIR = Path(__file__).resolve().parent.parent

sumo_spec = ModelSpec(
    name="sumo",
    adapter=SumoAdapter,
    timestep_length=timedelta(seconds=1),
    params={
        "sumo_config": str(_NASHVILLE_DIR / "data" / "sumo-0-23" / "main.sumocfg"),
    },
    dependencies=(),
)

charging_spec = ModelSpec(
    name="charging",
    adapter=ChargingAdapter,
    timestep_length=timedelta(minutes=1),
)

MODEL_SPECS = [sumo_spec, charging_spec]
