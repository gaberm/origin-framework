from typing import Mapping
from adapter import Adapter
from nashville.inputs.charging import arrived_vehicles, departed_vehicles
from nashville.models.charging.charging_model import ChargingModel
from nashville.outputs.charging import Station, PortStatus, ChargingEvent


class ChargingAdapter(Adapter):
    InputType = [arrived_vehicles, departed_vehicles]
    OutputType = [PortStatus, ChargingEvent]
    ConstantType = Station

    def __init__(self, name, timestep_length):
        super().__init__(name=name, timestep_length=timestep_length)
        self._charging_model = ChargingModel()

    def initialize(self):
        self._charging_model.load_data()

    def read_constants(self) -> list[Station]:
        return [
            Station(
                station_id=station_id,
                coords=[station["longitude"], station["latitude"]],
            )
            for station_id, station in self._charging_model.stations.items()
        ]

    def read_outputs(self) -> list[PortStatus | ChargingEvent]:
        outputs = []
        outputs += self._get_port_status()
        outputs += self._get_charging_event()
        return outputs

    def write_inputs(self, inputs: Mapping[str, list[dict]]):
        for row in inputs.get(arrived_vehicles.name, []):
            self._charging_model.add_vehicle(row["veh_id"], initial_soc=row["soc"])
        for row in inputs.get(departed_vehicles.name, []):
            self._charging_model.remove_vehicle(row["veh_id"])

    def _get_port_status(self) -> list[PortStatus]:
        outputs = []
        for event in self._charging_model.events:
            if event["time"] == self.model_time:
                outputs.append(
                    PortStatus(
                        port_id=event["port_id"],
                        station_id=self._charging_model.stations[event["port_id"]][
                            "station_id"
                        ],
                        load=event["load_kw"],
                        time=event["time"],
                    )
                )
        return outputs

    def _get_charging_event(self) -> list[ChargingEvent]:
        outputs = []
        for event in self._charging_model.completed_sessions:
            if event["time"] == (self.model_time - self._TIMESTEP_MIN):
                outputs.append(
                    ChargingEvent(
                        veh_id=event["veh_id"],
                        station_id=self._charging_model.stations[event["port_id"]][
                            "station_id"
                        ],
                        port_id=event["port_id"],
                        final_soc=event["soc"],
                        time=event["time"],
                    )
                )
        return outputs

    def advance(self) -> float:
        self._charging_model.advance_time(self.timestep_length)
        self._model_time += self.timestep_length
        return self._model_time

    def terminate(self):
        self._charging_model = None
