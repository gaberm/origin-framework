import json
import pandas as pd
import math


class ChargingModel:
    _STEP_SIZE = 1.0  # minutes

    def __init__(self):
        self.stations = None
        self.ports = None
        self.status = pd.DataFrame(
            columns=["station_id", "port_id", "vehicle_id", "current_soc", "max_soc"]
        )
        self.time = 0

    def load_data(self):
        with open("data/ev_stations.json", "r") as f:
            data = json.load(f)
        self.stations = pd.DataFrame.from_dict(data)[["id", "longitude", "latitude"]]
        self.ports = pd.DataFrame.from_records(
            [
                {
                    "station_id": station["id"],
                    "port_id": port["port_id"],
                    "level": port["level"],
                    "power_kw": port["power_kw"],
                }
                for station in data
                for port in station["ports"]
            ]
        )

    def add_car(
        self,
        vehicle_id: str,
        current_soc: float,
        max_soc: float,
        station_id: str = "random",
        port_id: str = "random",
    ):
        self.status = pd.concat(
            [
                self.status,
                pd.DataFrame(
                    [
                        {
                            "station_id": station_id,
                            "port_id": port_id,
                            "vehicle_id": vehicle_id,
                            "current_soc": current_soc,
                            "max_soc": max_soc,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    def remove_car(self, vehicle_id: str):
        self.status = self.status[self.status["vehicle_id"] != vehicle_id]

    def charge_step(
        self,
        power: float,
        current_soc: float,
        max_soc: float,
        efficiency: float = 0.9,
        k: float = 5.0,
    ):
        s = current_soc / max_soc
        effective_power = power if s < 0.8 else power * math.exp(-k * (s - 0.8))
        return min(
            current_soc + effective_power * (self._STEP_SIZE / 60) * efficiency, max_soc
        )

    def advance_time(self, duration: float):
        steps = round(duration / self._STEP_SIZE)
        merged = self.status.merge(
            self.ports[["station_id", "port_id", "power_kw"]],
            on=["station_id", "port_id"],
            how="left",
        )
        for _ in range(steps):
            self.status["current_soc"] = merged.apply(
                lambda row: self.charge_step(
                    row["power_kw"], row["current_soc"], row["max_soc"]
                ),
                axis=1,
            )
            merged["current_soc"] = self.status["current_soc"]
        self.time += duration

    def get_soc(self, vehicle_id: str):
        vehicle = self.status[self.status["vehicle_id"] == vehicle_id]
        if not vehicle.empty:
            return vehicle.iloc[0]["current_soc"]
        return None
