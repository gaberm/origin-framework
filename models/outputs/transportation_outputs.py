from dataclasses import dataclass
from models.outputs.outputs_decorator import record


@record(
    table="arrived_vehicles",
    key=("vehicle_id",),
    indexed=("arrival_time",),
)
@dataclass(frozen=True)
class ArrivedVehicle:
    vehicle_id: int
    soc_at_arrival: float
    road_id: str
    arrival_time: float


@record(
    table="departed_vehicles",
    key=("vehicle_id",),
    indexed=("departure_time",),
)
@dataclass(frozen=True)
class DepartedVehicle:
    vehicle_id: int
    departure_time: float


@dataclass(frozen=True)
class TransportationOutputs:
    arrived_vehicles: tuple[ArrivedVehicle, ...]
    departed_vehicles: tuple[DepartedVehicle, ...]

    def to_dict(self) -> dict:
        return {
            "arrived_vehicles": [
                {
                    "vehicle_id": v.vehicle_id,
                    "soc_at_arrival": v.soc_at_arrival,
                    "road_id": v.road_id,
                    "arrival_time": v.arrival_time,
                }
                for v in self.arrived_vehicles
            ],
            "departed_vehicles": [
                {
                    "vehicle_id": v.vehicle_id,
                    "departure_time": v.departure_time,
                }
                for v in self.departed_vehicles
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TransportationOutputs":
        return cls(
            arrived_vehicles=tuple(
                ArrivedVehicle(
                    vehicle_id=v["vehicle_id"],
                    soc_at_arrival=v["soc_at_arrival"],
                    road_id=v["road_id"],
                    arrival_time=v["arrival_time"],
                )
                for v in d.get("arrived_vehicles", [])
            ),
            departed_vehicles=tuple(
                DepartedVehicle(
                    vehicle_id=v["vehicle_id"],
                    departure_time=v["departure_time"],
                )
                for v in d.get("departed_vehicles", [])
            ),
        )
