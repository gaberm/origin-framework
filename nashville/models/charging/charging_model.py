import json
import random
import warnings
from dataclasses import dataclass
from pathlib import Path
import numpy as np

_SOC_TOLERANCE = 1e-9
_DEFAULT_STATIONS_FILE = Path(__file__).parent / "data" / "ev_stations.json"


@dataclass(frozen=True)
class ChargingCurve:
    """A car's SUMO charge curve and the SOC limit at its port.

    `soc_levels` are the SOC breakpoints and `rates_kw` the rate the car
    accepts at each, e.g. (0, 0.8, 1) and (150, 150, 25) for 150 kW up to 80%
    SOC, tapering to 25 kW at 100%. Charging stops at `soc_limit`, e.g. 0.9.
    """

    soc_levels: tuple[float, ...]
    rates_kw: tuple[float, ...]
    soc_limit: float

    def charge_step(
        self,
        soc: float,
        port_power_kw: float,
        capacity_kwh: float,
        efficiency: float,
        duration_s: float,
    ) -> tuple[float, float]:
        """Return the new SOC and the power drawn from the port in kW."""
        hours = duration_s / 3600
        # Interpolated between the breakpoints, as SUMO does.
        curve_rate_kw = float(np.interp(soc, self.soc_levels, self.rates_kw))
        rate_kw = min(curve_rate_kw, port_power_kw * efficiency)
        charge_added_kwh = min(
            rate_kw * hours,
            capacity_kwh * max(self.soc_limit - soc, 0.0),
        )
        load_kw = charge_added_kwh / (hours * efficiency)
        return soc + charge_added_kwh / capacity_kwh, load_kw


# eq=False: sessions compare and hash by identity, so two sessions with equal
# fields stay distinct.
@dataclass(slots=True, eq=False)
class ChargingSession:
    """A car's occupancy of a port, whether charging or just blocking it."""

    port_id: str
    started_at: float  # seconds
    veh_id: str
    driver_id: str
    soc: float
    capacity_kwh: float
    curve: ChargingCurve

    def charge(
        self, port_power_kw: float, efficiency: float, duration_s: float
    ) -> float:
        """Charge the car and return the power drawn from the port in kW."""
        self.soc, load_kw = self.curve.charge_step(
            self.soc, port_power_kw, self.capacity_kwh, efficiency, duration_s
        )
        return load_kw

    def completed_record(self, finished_at: float, end_reason: str) -> dict:
        return {
            "port_id": self.port_id,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "veh_id": self.veh_id,
            "driver_id": self.driver_id,
            "final_soc": self.soc,
            "final_charge_kwh": self.soc * self.capacity_kwh,
            "end_reason": end_reason,
        }


class ChargingInfrastructure:
    """The charging stations and their ports."""

    def __init__(self, stations: dict[str, dict], ports: dict[str, dict]):
        self.stations = stations
        self.ports = ports
        self._ports_by_station: dict[str, list[str]] = {}
        for port_id, port in ports.items():
            self._ports_by_station.setdefault(port["station_id"], []).append(port_id)

    @classmethod
    def from_json(cls, path: str | Path) -> "ChargingInfrastructure":
        with open(path, "r") as stations_file:
            station_records = json.load(stations_file)
        stations = {}
        ports = {}
        for station in station_records:
            stations[station["id"]] = {
                "source": station["source"],
                "source_id": station["source_id"],
                "longitude": station["longitude"],
                "latitude": station["latitude"],
            }
            for port in station["ports"]:
                ports[port["port"]] = {
                    "station_id": station["id"],
                    "level": port["level"],
                    "power_kw": port["power_kw"],
                }
        return cls(stations, ports)

    def port_ids_at_station(self, station_id: str) -> list[str]:
        return self._ports_by_station.get(station_id, [])

    def get_ports(
        self,
        *,
        port_ids: str | list[str] | None = None,
        station_ids: str | list[str] | None = None,
        levels: str | list[str] | None = None,
    ) -> list[dict]:
        records = [{"port_id": port_id, **port} for port_id, port in self.ports.items()]
        return self._filter(
            records, port_id=port_ids, station_id=station_ids, level=levels
        )

    def get_stations(
        self,
        *,
        station_ids: str | list[str] | None = None,
    ) -> list[dict]:
        records = [
            {"station_id": station_id, **station}
            for station_id, station in self.stations.items()
        ]
        return self._filter(records, station_id=station_ids)

    @staticmethod
    def _filter(records: list[dict], **criteria) -> list[dict]:
        """Keep the records that match every given criterion.

        A criterion is a single value or a list of accepted values, e.g.
        `station_id="s1"` or `station_id=["s1", "s2"]`. Criteria left at
        None are not filtered on.
        """
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
    """Computes the charge cars gain and the power they draw per time step.

    Cars charge at the rate of their SUMO charge curve or the port's power,
    whichever is lower, and stop at the port's SOC limit.
    """

    def __init__(
        self,
        stations_file: str | Path = _DEFAULT_STATIONS_FILE,
        leave_at_soc_limit: bool = True,
        raise_if_unavailable: bool = False,
        seed: int = 42,
        efficiency: float = 0.95,
        dc_soc_limit: float = 0.9,
        ac_soc_limit: float = 0.9,
    ):
        """`stations_file` is the stations JSON (see ChargingInfrastructure.from_json).

        `leave_at_soc_limit` decides whether cars free the port (True) or keep
        occupying it (False) once they reach their SOC limit.

        `raise_if_unavailable` decides whether a car sent to a station with no
        free port raises an error (True) or is skipped with a warning (False).

        `seed` is the random seed for the choice of port.

        `efficiency` is the share of port power reaching the battery.

        `dc_soc_limit` is the SOC limit on DC fast chargers.

        `ac_soc_limit` is the SOC limit on level 1 and 2 chargers."""
        self.stations_file = stations_file
        self.leave_at_soc_limit = leave_at_soc_limit
        self.raise_if_unavailable = raise_if_unavailable
        self._random = random.Random(seed)
        self.efficiency = efficiency
        self.dc_soc_limit = dc_soc_limit
        self.ac_soc_limit = ac_soc_limit

        self.infrastructure: ChargingInfrastructure | None = None
        self.time_s = 0.0
        self.active_sessions: list[ChargingSession] = []
        self.latest_completed_sessions: list[dict] = []
        self._latest_power_logs: list[dict] = []

    def load_data(self):
        self.infrastructure = ChargingInfrastructure.from_json(self.stations_file)

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
    ):
        """`charge_level_table` and `charge_curve_kw` are the vehicle's SUMO
        charge curve: the SOC breakpoints and the rate it accepts at each."""
        self._validate_port_or_station(port_id, station_id)
        self._validate_charge_curve(veh_id, charge_level_table, charge_curve_kw)
        if port_id is None:
            port_id = self._random_available_port(station_id)
            if not self._check_port_available(port_id, station_id):
                return
        self.active_sessions.append(
            ChargingSession(
                port_id=port_id,
                started_at=self.time_s,
                veh_id=veh_id,
                driver_id=driver_id,
                soc=soc,
                capacity_kwh=capacity_kwh,
                curve=ChargingCurve(
                    soc_levels=tuple(charge_level_table),
                    rates_kw=tuple(charge_curve_kw),
                    soc_limit=self._soc_limit(port_id),
                ),
            )
        )

    @staticmethod
    def _validate_port_or_station(port_id: str | None, station_id: str | None):
        if port_id is None and station_id is None:
            raise ValueError("must provide either port_id or station_id")

    @staticmethod
    def _validate_charge_curve(
        veh_id: str, charge_level_table: list[float], charge_curve_kw: list[float]
    ):
        if not charge_level_table or len(charge_level_table) != len(charge_curve_kw):
            raise ValueError(
                f"vehicle '{veh_id}' has a charge curve of "
                f"{len(charge_level_table)} SOC breakpoints and "
                f"{len(charge_curve_kw)} rates; they must match and be non-empty"
            )

    def _check_port_available(self, port_id: str | None, station_id: str) -> bool:
        """Return whether a port was found at the station; if not, raise or
        warn depending on `raise_if_unavailable`."""
        if port_id is not None:
            return True
        if self.raise_if_unavailable:
            raise ValueError(f"no available port at station '{station_id}'")
        # The time makes each warning unique, so Python's default filter
        # shows every full station rather than only its first.
        warnings.warn(
            f"no available port at station '{station_id}' at time {self.time_s:g} s",
            RuntimeWarning,
        )
        return False

    def _soc_limit(self, port_id: str) -> float:
        if self.infrastructure.ports[port_id]["level"] == "dc_fast":
            return self.dc_soc_limit
        return self.ac_soc_limit

    def _random_available_port(self, station_id: str) -> str | None:
        occupied_ports = {session.port_id for session in self.active_sessions}
        available_ports = [
            port_id
            for port_id in self.infrastructure.port_ids_at_station(station_id)
            if port_id not in occupied_ports
        ]
        if not available_ports:
            return None
        return self._random.choice(available_ports)

    def remove_vehicle(self, driver_id: str, end_reason: str = "removed"):
        """End all open sessions of the driver."""
        ending = [
            session
            for session in self.active_sessions
            if session.driver_id == driver_id
        ]
        self._complete_sessions(ending, end_reason=end_reason)

    def _complete_sessions(self, sessions: list[ChargingSession], end_reason: str):
        for session in sessions:
            self.latest_completed_sessions.append(
                session.completed_record(self.time_s, end_reason)
            )
        # Rebuilt rather than removed one by one, which would skip sessions
        # when `sessions` is `active_sessions` itself.
        ended = set(sessions)
        self.active_sessions = [
            session for session in self.active_sessions if session not in ended
        ]

    def advance_time(self, duration_s: float):
        self.time_s += duration_s
        self._latest_power_logs = []
        for session in self.active_sessions:
            port = self.infrastructure.ports[session.port_id]
            load_kw = session.charge(port["power_kw"], self.efficiency, duration_s)
            self._latest_power_logs.append(
                {
                    "time": self.time_s,
                    "veh_id": session.veh_id,
                    "driver_id": session.driver_id,
                    "port_id": session.port_id,
                    "load_kw": load_kw,
                }
            )
        if self.leave_at_soc_limit:
            self._complete_sessions_at_limit()

    def _complete_sessions_at_limit(self):
        # A step lands on the limit up to float rounding, hence the tolerance.
        at_limit = [
            session
            for session in self.active_sessions
            if session.soc >= session.curve.soc_limit - _SOC_TOLERANCE
        ]
        self._complete_sessions(at_limit, end_reason="limit_reached")

    def get_latest_power_logs(self) -> list[dict]:
        """Power drawn per session in the latest step."""
        return self._latest_power_logs

    def get_latest_completed_sessions(self) -> list[dict]:
        sessions = self.latest_completed_sessions
        self.latest_completed_sessions = []
        return sessions

    def terminate(self) -> list[dict]:
        """End every open session and return all completed sessions not yet
        collected."""
        self._complete_sessions(self.active_sessions, end_reason="terminated")
        return self.get_latest_completed_sessions()
