from dataclasses import dataclass


@dataclass(frozen=True)
class VehicleSoc:
    vehicle_id: int
    soc: float
    stop_time: float


@dataclass(frozen=True)
class TransportationInputs:
    vehicles_soc: tuple[VehicleSoc, ...]

    def to_dict(self) -> dict:
        return {
            "vehicles_soc": [
                {"vehicle_id": v.vehicle_id, "soc": v.soc, "stop_time": v.stop_time}
                for v in self.vehicles_soc
            ]
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TransportationInputs":
        return cls(
            vehicles_soc=tuple(
                VehicleSoc(
                    vehicle_id=v["vehicle_id"],
                    soc=v["soc"],
                    stop_time=v["stop_time"],
                )
                for v in d.get("vehicles_soc", [])
            )
        )
