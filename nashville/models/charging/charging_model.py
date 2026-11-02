import json
import random
import warnings
import numpy as np
from dataclasses import dataclass
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
_SOC_TOLERANCE = 1e-9


@dataclass(frozen=True)
class ChargingCurve:
    """How fast a car charges, as a function of its state of charge."""

    random.seed(42)
    efficiency: float = 0.95
    # Charging stops at these limits. Whether the car then leaves its port is
    # up to ChargingModel's `leave_at_soc_limit`.
    dc_soc_limit: float = 0.9  # DC fast chargers
    ac_soc_limit: float = 0.9  # Level 1 and 2 chargers

    def soc_limit(self, level: str) -> float:
        if level == "dc_fast":
            return self.dc_soc_limit
        return self.ac_soc_limit

    def step(
        self,
        soc: np.ndarray,
        port_power_kw: np.ndarray,
        vehicle_rate_kw: np.ndarray,
        capacity_kwh: np.ndarray,
        soc_limit: np.ndarray,
        duration_s: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return the new state of charge and the power drawn in kW.

        `vehicle_rate_kw` is what the car accepts at its current SOC, read from
        its SUMO charge curve. As in SUMO, the station's efficiency applies to
        the port's power, and whichever of the two is lower reaches the
        battery."""
        hours = duration_s / 3600
        rate_kw = np.minimum(vehicle_rate_kw, port_power_kw * self.efficiency)
        charge_added_kwh = np.minimum(
            rate_kw * hours,
            capacity_kwh * np.maximum(soc_limit - soc, 0.0),
        )
        # Report only the power actually drawn: less in the step that reaches
        # the limit, 0 kW for a car that is already there.
        load_kw = charge_added_kwh / (hours * self.efficiency)
        return soc + charge_added_kwh / capacity_kwh, load_kw


class ChargingInfrastructure:
    """The charging stations and their ports."""

    def __init__(self, stations: dict[str, dict], ports: dict[str, dict]):
        self.stations = stations
        self.ports = ports
        self._ports_by_station: dict[str, list[str]] = {
            station_id: [] for station_id in stations
        }
        for port_id, port in ports.items():
            self._ports_by_station[port["station_id"]].append(port_id)

    @classmethod
    def from_json(cls, path: Path) -> "ChargingInfrastructure":
        with open(path, "r") as f:
            data = json.load(f)
        stations = {}
        ports = {}
        for station in data:
            stations[station["id"]] = {
                "source": station["source"],
                "source_id": station["source_id"],
                "longitude": station["longitude"],
                "latitude": station["latitude"],
            }
            for port_data in station["ports"]:
                ports[port_data["port"]] = {
                    "station_id": station["id"],
                    "level": port_data["level"],
                    "power_kw": port_data["power_kw"],
                }
        return cls(stations, ports)

    def ports_at(self, station_id: str) -> list[str]:
        return self._ports_by_station.get(station_id, [])

    def station_id(self, port_id: str) -> str:
        return self.ports[port_id]["station_id"]

    def get_ports(
        self,
        *,
        port_id: str | list[str] | None = None,
        station_id: str | list[str] | None = None,
        level: str | list[str] | None = None,
    ) -> list[dict]:
        records = [{"port_id": k, **v} for k, v in self.ports.items()]
        return self._filter(
            records, port_id=port_id, station_id=station_id, level=level
        )

    def get_stations(
        self,
        *,
        station_id: str | list[str] | None = None,
    ) -> list[dict]:
        records = [{"station_id": k, **v} for k, v in self.stations.items()]
        return self._filter(records, station_id=station_id)

    @staticmethod
    def _filter(records: list[dict], **criteria) -> list[dict]:
        """Keep records matching every criterion; a criterion is a single
        value or a list of accepted values, and None matches everything."""
        allowed = {
            key: set(value) if isinstance(value, (list, tuple, set)) else {value}
            for key, value in criteria.items()
            if value is not None
        }
        return [
            record
            for record in records
            if all(record[key] in values for key, values in allowed.items())
        ]


class ChargingModel:
    def __init__(self, leave_at_soc_limit: bool = True, **curve_params):
        """`leave_at_soc_limit` ends a session once the car reaches its SOC
        limit, freeing the port; otherwise the car keeps blocking the port until
        it leaves. `curve_params` are passed to `ChargingCurve`."""
        self.leave_at_soc_limit = leave_at_soc_limit
        self.curve = ChargingCurve(**curve_params)
        self.infrastructure: ChargingInfrastructure | None = None
        self.time = 0.0  # seconds
        self.active_sessions = []
        self.latest_completed_session = []
        # Power drawn per session in the latest step only.
        self._latest_power_logs: list[dict] = []

    def load_data(self):
        self.infrastructure = ChargingInfrastructure.from_json(
            _DATA_DIR / "ev_stations.json"
        )

    def add_vehicle(
        self,
        veh_id: str,
        driver_id: str,
        soc: float,
        capacity_kwh: float,
        charge_level_table: list[float],
        charge_curve_kw: list[float],
        port_id: str | None = None,
        station_id: str | None = None,
        raise_if_unavailable: bool = False,
    ):
        """`charge_level_table` and `charge_curve_kw` are the vehicle's SUMO
        charge curve: the SOC breakpoints and the rate it accepts at each."""
        if port_id is None and station_id is None:
            raise ValueError("must provide either port_id or station_id")
        if not charge_level_table or len(charge_level_table) != len(charge_curve_kw):
            raise ValueError(
                f"vehicle '{veh_id}' has a charge curve of "
                f"{len(charge_level_table)} SOC breakpoints and "
                f"{len(charge_curve_kw)} rates; they must match and be non-empty"
            )
        if port_id is None:
            port_id = self._random_available_port(station_id, raise_if_unavailable)
            if port_id is None:
                return
        self.active_sessions.append(
            {
                "port_id": port_id,
                "started_at": self.time,
                "veh_id": veh_id,
                "driver_id": driver_id,
                "soc": soc,
                "capacity_kwh": capacity_kwh,
                # Hashable, so sessions can be grouped by curve in a dict.
                "charge_curve": (tuple(charge_level_table), tuple(charge_curve_kw)),
                "soc_limit": self.curve.soc_limit(
                    self.infrastructure.ports[port_id]["level"]
                ),
            }
        )

    def _random_available_port(
        self, station_id: str, raise_if_unavailable: bool
    ) -> str | None:
        occupied_ports = {session["port_id"] for session in self.active_sessions}
        available_ports = [
            port_id
            for port_id in self.infrastructure.ports_at(station_id)
            if port_id not in occupied_ports
        ]
        if not available_ports:
            if raise_if_unavailable:
                raise ValueError(f"no available port at station '{station_id}'")
            warnings.warn(
                f"no available port at station '{station_id}'", RuntimeWarning
            )
            return None
        return random.choice(available_ports)

    def remove_vehicle(self, driver_id: str, end_reason: str = "removed"):
        """End all open sessions of the driver."""
        ending = [
            session
            for session in self.active_sessions
            if session["driver_id"] == driver_id
        ]
        if not ending:
            return
        self._complete_sessions(ending, end_reason=end_reason)

    def _complete_sessions(self, sessions: list[dict], end_reason: str):
        """Move `sessions` from the open to the completed sessions."""
        for session in sessions:
            self.latest_completed_session.append(
                {
                    "port_id": session["port_id"],
                    "started_at": session["started_at"],
                    "finished_at": self.time,
                    "veh_id": session["veh_id"],
                    "driver_id": session["driver_id"],
                    "final_soc": session["soc"],
                    "final_charge_kwh": session["soc"] * session["capacity_kwh"],
                    "end_reason": end_reason,
                }
            )
        # Rebuilt rather than removed one by one, which would skip sessions
        # when `sessions` is `active_sessions` itself.
        ended = {id(session) for session in sessions}
        self.active_sessions = [
            session for session in self.active_sessions if id(session) not in ended
        ]

    def advance_time(self, duration_s: float):
        """Charge all active sessions for `duration_s` seconds as one step."""
        self.time += duration_s
        self._latest_power_logs = []
        if not self.active_sessions:
            return

        soc, port_power_kw, max_capacity_kwh, soc_limit = self._get_session_arrays()
        soc, load_kw = self.curve.step(
            soc,
            port_power_kw,
            self._vehicle_rate_kw(soc),
            max_capacity_kwh,
            soc_limit,
            duration_s,
        )
        self._record_power_logs(load_kw)
        self._update_soc(soc)
        if self.leave_at_soc_limit:
            self._complete_sessions_at_limit()

    def _complete_sessions_at_limit(self):
        # The step lands on the limit up to float rounding, hence the tolerance.
        at_limit = [
            session
            for session in self.active_sessions
            if session["soc"] >= session["soc_limit"] - _SOC_TOLERANCE
        ]
        if at_limit:
            self._complete_sessions(at_limit, end_reason="limit_reached")

    def _get_session_arrays(self) -> tuple[np.ndarray, ...]:
        ports = self.infrastructure.ports
        return (
            np.array([session["soc"] for session in self.active_sessions]),
            np.array(
                [
                    ports[session["port_id"]]["power_kw"]
                    for session in self.active_sessions
                ]
            ),
            np.array([session["capacity_kwh"] for session in self.active_sessions]),
            np.array([session["soc_limit"] for session in self.active_sessions]),
        )

    def _vehicle_rate_kw(self, soc: np.ndarray) -> np.ndarray:
        """What each car accepts at its current SOC, interpolated from its
        charge curve as SUMO does. Sessions are grouped by curve, so this costs
        one np.interp call per vehicle type rather than one per session."""
        sessions_by_curve: dict[tuple, list[int]] = {}
        for index, session in enumerate(self.active_sessions):
            sessions_by_curve.setdefault(session["charge_curve"], []).append(index)
        rate_kw = np.empty(len(self.active_sessions))
        for (levels, rates), indices in sessions_by_curve.items():
            indices = np.array(indices)
            rate_kw[indices] = np.interp(soc[indices], levels, rates)
        return rate_kw

    def _record_power_logs(self, load_kw: np.ndarray):
        self._latest_power_logs = [
            {
                "time": self.time,
                "veh_id": session["veh_id"],
                "driver_id": session["driver_id"],
                "port_id": session["port_id"],
                "load_kw": float(load),
            }
            for session, load in zip(self.active_sessions, load_kw)
        ]

    def _update_soc(self, soc: np.ndarray):
        for session, new_soc in zip(self.active_sessions, soc):
            session["soc"] = float(new_soc)

    def get_latest_power_logs(self) -> list[dict]:
        """Power drawn per session in the latest step."""
        return self._latest_power_logs

    def get_latest_completed_sessions(self) -> list[dict]:
        sessions = self.latest_completed_session
        self.latest_completed_session = []
        return sessions

    def get_station_id(self, port_id: str) -> str:
        return self.infrastructure.station_id(port_id)

    def terminate(self) -> list[dict]:
        """End every open session and return all completed sessions not yet
        collected."""
        # dict rather than set: keeps the output order reproducible.
        for driver_id in dict.fromkeys(s["driver_id"] for s in self.active_sessions):
            self.remove_vehicle(driver_id, end_reason="terminated")
        return self.get_latest_completed_sessions()
