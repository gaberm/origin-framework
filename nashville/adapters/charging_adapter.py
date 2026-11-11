from datetime import timedelta
from typing import Mapping
from adapter import Adapter
from nashville.inputs.charging import (
    arrived_bevs,
    arrived_phevs,
    departed_vehicles,
)
from nashville.models.charging.charging_model import ChargingModel
from nashville.outputs.charging import ChargingSession, ChargingStep, Port, Station


class ChargingAdapter(Adapter):
    input_types = [
        arrived_bevs,
        arrived_phevs,
        departed_vehicles,
    ]
    output_types = [ChargingStep, ChargingSession, Station, Port]

    def __init__(self, name, run_id, timestep_length, active_from, **model_params):
        super().__init__(
            name=name,
            run_id=run_id,
            timestep_length=timestep_length,
            active_from=active_from,
        )
        self._charging_model = ChargingModel(**model_params)

    def initialize(self) -> list[Station | Port]:
        self._charging_model.load_data()
        return self._infrastructure_records()

    def current_time(self) -> timedelta:
        return timedelta(seconds=self._charging_model.time_s)

    def _infrastructure_records(self) -> list[Station | Port]:
        infrastructure = self._charging_model.infrastructure
        port_counts = {
            station_id: {"ac": 0, "dc": 0} for station_id in infrastructure.stations
        }
        for port in infrastructure.ports.values():
            kind = "dc" if port["level"] == "dc_fast" else "ac"
            port_counts[port["station_id"]][kind] += 1
        stations = [
            Station(
                station_id=station_id,
                source_id=str(station["source_id"]),
                coords=[station["longitude"], station["latitude"]],
                ac_ports=port_counts[station_id]["ac"],
                dc_ports=port_counts[station_id]["dc"],
                start_s=None,
            )
            for station_id, station in infrastructure.stations.items()
        ]
        ports = [
            Port(port_id=port_id, station_id=port["station_id"], start_s=None)
            for port_id, port in infrastructure.ports.items()
        ]
        return [*stations, *ports]

    def read_outputs(self) -> list[ChargingStep | ChargingSession]:
        outputs = []
        outputs += self._get_charging_steps()
        outputs += self._get_charging_sessions()
        return outputs

    def write_inputs(self, inputs: Mapping[str, list[dict]]):
        arrivals = [
            *inputs.get(arrived_bevs.name, []),
            *inputs.get(arrived_phevs.name, []),
        ]
        departures = inputs.get(departed_vehicles.name, [])
        events = [(row["time_s"], False, row) for row in arrivals]
        events += [(row["time_s"], True, row) for row in departures]
        for _, is_departure, row in sorted(events, key=lambda event: event[:2]):
            if is_departure:
                self._charging_model.remove_vehicle(row["driver_id"])
            else:
                self._add_vehicle(row)

    def _add_vehicle(self, row: dict):
        # A full station only warns (see ChargingModel.add_vehicle); anything
        # else that fails is a bug and should stop the run.
        self._charging_model.add_vehicle(
            veh_id=row["veh_id"],
            driver_id=row["driver_id"],
            soc=row["charge_wh"] / row["capacity_wh"],
            capacity_kwh=row["capacity_wh"] / 1000,
            charge_level_table=row["charge_level_table"],
            charge_curve_kw=[rate_w / 1000 for rate_w in row["charge_curve_table"]],
            station_id=row["station_id"],
        )

    def _get_charging_steps(self) -> list[ChargingStep]:
        outputs = []
        for event in self._charging_model.get_latest_power_logs():
            outputs.append(
                ChargingStep(
                    port_id=event["port_id"],
                    driver_id=event["driver_id"],
                    load_kw=event["load_kw"],
                    time_s=event["time"],
                )
            )
        return outputs

    def _get_charging_sessions(self) -> list[ChargingSession]:
        return self._to_charging_sessions(
            self._charging_model.get_latest_completed_sessions()
        )

    def _to_charging_sessions(self, sessions: list[dict]) -> list[ChargingSession]:
        time = self.current_time()
        return [
            ChargingSession(
                veh_id=session["veh_id"],
                driver_id=session["driver_id"],
                port_id=session["port_id"],
                final_charge_wh=session["final_charge_kwh"] * 1000,
                end_reason=session["end_reason"],
                time_s=time,
            )
            for session in sessions
        ]

    def advance(self):
        self._charging_model.advance_time(self.timestep_length.total_seconds())

    def terminate(self) -> list[ChargingSession]:
        # Converted before the model is dropped: the station lookup needs it.
        sessions = self._to_charging_sessions(self._charging_model.terminate())
        self._charging_model = None
        return sessions
