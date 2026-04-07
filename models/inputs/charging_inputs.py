from dataclasses import dataclass


@dataclass(frozen=True)
class VehicleToAdd:
    vehicle_id: int
    initial_soc: float


@dataclass(frozen=True)
class VehicleToRemove:
    vehicle_id: int


@dataclass(frozen=True)
class ChargingInputs:
    vehicles_to_add: tuple[VehicleToAdd, ...]
    vehicles_to_remove: tuple[VehicleToRemove, ...]

    def to_dict(self) -> dict:
        return {
            "vehicles_to_add": [
                {"vehicle_id": v.vehicle_id, "initial_soc": v.initial_soc}
                for v in self.vehicles_to_add
            ],
            "vehicles_to_remove": [
                {"vehicle_id": v.vehicle_id} for v in self.vehicles_to_remove
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChargingInputs":
        return cls(
            vehicles_to_add=tuple(
                VehicleToAdd(vehicle_id=v["vehicle_id"], initial_soc=v["initial_soc"])
                for v in d.get("vehicles_to_add", [])
            ),
            vehicles_to_remove=tuple(
                VehicleToRemove(vehicle_id=v["vehicle_id"])
                for v in d.get("vehicles_to_remove", [])
            ),
        )
