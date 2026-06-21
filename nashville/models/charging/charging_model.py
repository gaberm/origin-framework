import json
import numpy as np
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"


class ChargingModel:
    _TIMESTEP_MIN = 1.0

    def __init__(self, efficiency: float = 0.95, taper_k: float = 5.0):
        self.efficiency = efficiency
        self.taper_k = taper_k
        self.time = 0.0
        self.stations: dict[str, dict] = {}
        self.ports: dict[int, dict] = {}
        self.active_sessions = []
        self.completed_sessions = []
        self.events = []

    def load_data(self):
        with open(_DATA_DIR / "ev_stations.json", "r") as f:
            data = json.load(f)
        for station in data:
            self.stations[station["id"]] = {
                "longitude": station["longitude"],
                "latitude": station["latitude"],
            }
            for port_data in station["ports"]:
                self.ports[port_data["port"]] = {
                    "station_id": station["id"],
                    "level": port_data["level"],
                    "power_kw": port_data["power_kw"],
                }

    def add_vehicle(
        self,
        veh_id: str,
        soc: float,
        capacity_kwh: float,
        charging_power_kw: float,
        port_id: int = None,
    ):
        self.active_sessions.append(
            {
                "port_id": port_id,
                "started_at": self.time,
                "veh_id": veh_id,
                "soc": soc,
                "capacity_kwh": capacity_kwh,
                "charging_power_kw": charging_power_kw,
            }
        )

    def remove_vehicle(self, veh_id: str):
        for session in self.active_sessions:
            if session["veh_id"] == veh_id:
                self.completed_sessions.append(
                    {
                        "port_id": session["port_id"],
                        "started_at": self.time,
                        "finished_at": self.time,
                        "veh_id": veh_id,
                        "final_soc": session["soc"],
                    }
                )
                self.active_sessions.remove(session)

    def advance_time(self, duration: float = _TIMESTEP_MIN):
        steps = round(duration / self._TIMESTEP_MIN)
        if not self.active_sessions:
            self.time += duration
            return

        soc, port_power_kw, charging_power_kw, max_capacity_kwh = (
            self._get_session_arrays()
        )
        for _ in range(steps):
            soc, load_kw = self._charge_step(
                soc, port_power_kw, charging_power_kw, max_capacity_kwh
            )
            self.time += self._TIMESTEP_MIN
            self._record_events(load_kw)
        self._update_soc(soc)

    def _get_session_arrays(self) -> tuple[np.ndarray, ...]:
        return (
            np.array([session["soc"] for session in self.active_sessions]),
            np.array(
                [
                    self.ports[session["port"]]["power_kw"]
                    for session in self.active_sessions
                ]
            ),
            np.array(
                [session["charging_power_kw"] for session in self.active_sessions]
            ),
            np.array([session["capacity_kwh"] for session in self.active_sessions]),
        )

    def _charge_step(
        self,
        soc: np.ndarray,
        port_power_kw: np.ndarray,
        charging_power_kw: np.ndarray,
        capacity_kwh: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        capped_rate_kw = np.minimum(charging_power_kw, port_power_kw)
        taper_factor = np.where(soc >= 0.8, np.exp(-self.taper_k * (soc - 0.8)), 1.0)
        load_kw = capped_rate_kw * taper_factor
        charge_added_kwh = np.minimum(
            load_kw * (self._TIMESTEP_MIN / 60) * self.efficiency,
            capacity_kwh * (1 - soc),
        )
        return soc + charge_added_kwh / capacity_kwh, load_kw

    def _record_events(self, load_kw: np.ndarray):
        for session, load in zip(self.active_sessions, load_kw):
            self.events.append(
                {
                    "veh_id": session["veh_id"],
                    "port_id": session["port_id"],
                    "time": self.time,
                    "load_kw": load,
                }
            )

    def _update_soc(self, new_soc: np.ndarray):
        for session, new_soc in zip(self.active_sessions, new_soc):
            session["soc"] = float(new_soc)

    def get_station_id(self, port_id: str) -> str:
        return self.ports[port_id]["station_id"]

    def get_ports(
        self,
        *,
        port_id: int | list[int] = None,
        station_id: str | list[str] = None,
        level: int | list[int] = None,
    ) -> list[dict]:
        records = [{"port_id": k, **v} for k, v in self.ports.items()]
        return self._filter(
            records, port_id=port_id, station_id=station_id, level=level
        )

    def get_stations(
        self,
        *,
        station_id: str | list[str] = None,
    ) -> list[dict]:
        records = [{"station_id": k, **v} for k, v in self.stations.items()]
        return self._filter(records, station_id=station_id)
