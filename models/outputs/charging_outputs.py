from dataclasses import dataclass
from models.outputs.outputs_decorator import record


@record(
    table="charging_vehicles",
    key=("vehicle_id", "timestamp"),
    indexed=("timestamp",),
)
@dataclass(frozen=True)
class ChargingVehicle:
    vehicle_id: int
    soc: float
    timestamp: float


@record(
    table="charged_vehicles",
    key=("vehicle_id",),
    indexed=("ended_at",),
)
@dataclass(frozen=True)
class ChargedVehicle:
    vehicle_id: int
    soc: float
    ended_at: float


@dataclass(frozen=True)
class ChargingOutputs:
    charging_vehicles: tuple[ChargingVehicle, ...]
    charged_vehicles: tuple[ChargedVehicle, ...]

    def to_dict(self) -> dict:
        return {
            "charging_vehicles": [
                {
                    "vehicle_id": v.vehicle_id,
                    "soc": v.soc,
                    "timestamp": v.timestamp,
                }
                for v in self.charging_vehicles
            ],
            "charged_vehicles": [
                {
                    "vehicle_id": v.vehicle_id,
                    "soc": v.soc,
                    "ended_at": v.ended_at,
                }
                for v in self.charged_vehicles
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChargingOutputs":
        return cls(
            charging_vehicles=tuple(
                ChargingVehicle(
                    vehicle_id=v["vehicle_id"],
                    soc=v["soc"],
                    timestamp=v["timestamp"],
                )
                for v in d.get("charging_vehicles", [])
            ),
            charged_vehicles=tuple(
                ChargedVehicle(
                    vehicle_id=v["vehicle_id"],
                    soc=v["soc"],
                    ended_at=v["ended_at"],
                )
                for v in d.get("charged_vehicles", [])
            ),
        )
