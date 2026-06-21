from typing import Mapping
from adapter import Adapter
from nashville.inputs.charging import ArrivedVehicles, DepartedVehicles
from nashville.models.charging.charging_model import ChargingModel
from nashville.outputs.charging import Station, PortStatus, ChargingEvent


class ChargingAdapter(Adapter):
    InputType = [ArrivedVehicles, DepartedVehicles]
    OutputType = [PortStatus, ChargingEvent]
    ConstantType = Station

    def __init__(self, name, timestep_length):
        self.name = name
        self.timestep_length = timestep_length
        self._charging_model = ChargingModel()

    def initialize(self):
        self._charging_model.load_data()

    def read_constants(self) -> list[Station]:
        return [
            Station(
                station_id=station_id,
                coords=(station["longitude"], station["latitude"]),
            )
            for station_id, station in self._charging_model.stations.items()
        ]

    def read_outputs(self) -> list[type[PortStatus] | type[ChargingEvent]]:
        outputs = []
        outputs += self._get_port_status()
        outputs += self._get_charging_event()
        return outputs

    def write_inputs(self, inputs: Mapping[str, list[dict]]):
        for row in inputs.get(ArrivedVehicles.key, []):
            self._charging_model.add_vehicle(row["veh_id"], initial_soc=row["soc"])
        for row in inputs.get(DepartedVehicles.key, []):
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
                        load_kw=event["load_kw"],
                        timestamp=event["time"],
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
                        timestamp=event["time"],
                    )
                )
        return outputs

    def advance(self):
        self._charging_model.advance_time()
        self._model_time += self.timestep_length

    def terminate(self):
        self._charging_model = None
