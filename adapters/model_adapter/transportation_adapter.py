from adapters import BaseAdapter
from models.outputs import (
    DepartedVehicle,
    TransportationOutputs,
    ArrivedVehicle,
)
from models.inputs import TransportationInputs
import traci
import re
from traci.constants import VAR_ROAD_ID


class TransportationAdapter(BaseAdapter):
    InputType = TransportationInputs
    OutputType = TransportationOutputs

    @classmethod
    def from_config(cls, model_cfg) -> "TransportationAdapter":
        return cls(
            name=model_cfg.name,
            timestep_length=model_cfg.timestep_length,
            sumo_config=model_cfg.sumo_config,
        )

    def __init__(self, name, timestep_length, sumo_config):
        super().__init__(
            name=name,
            timestep_length=timestep_length,
        )
        self._sumo_config = sumo_config
        self._traci = None

        self._last_edge_by_vid: dict[str, str] = {}

    def initialize(self):
        traci.start(["sumo", "-c", self._sumo_config])
        self._traci = traci
        self._timestep_length = self._traci.simulation.getDeltaT()

    def read_outputs(self) -> TransportationOutputs:
        return TransportationOutputs(
            departed_vehicles=self._get_departed_vehicles(),
            arrived_vehicles=self._get_arrived_vehicles(),
        )

    def _get_departed_vehicles(self) -> tuple[DepartedVehicle, ...]:
        return tuple(
            DepartedVehicle(
                vehicle_id=int(re.sub(r"^(person_|truck_)", "", vid)),
                departure_time=self.model_time,
            )
            for vid in self._traci.simulation.getDepartedIDList()
        )

    def _get_arrived_vehicles(self) -> tuple[ArrivedVehicle, ...]:
        return tuple(
            ArrivedVehicle(
                vehicle_id=int(re.sub(r"^(person_|truck_)", "", vid)),
                # soc_at_arrival=float(
                #     self._traci.vehicle.getParameter(
                #         vid, "device.battery.actualBatteryCapacity"
                #     )
                # ),
                soc_at_arrival=0.0,
                road_id=self._last_edge_by_vid.get(vid, "unknown"),
                arrival_time=self.model_time,
            )
            for vid in self._traci.simulation.getArrivedIDList()
        )

    def write_inputs(self, inputs: TransportationInputs):
        for vehicle_id, new_soc in inputs.vehicles_soc:
            self._set_vehicle_soc(str(vehicle_id), new_soc)

    def _set_vehicle_soc(self, vehicle_id: str, soc: float):
        self._traci.vehicle.setParameter(
            vehicle_id, "device.battery.actualBatteryCapacity", str(soc * 100)
        )

    def advance(self, dt: float):
        if dt % self.timestep_length != 0:
            raise ValueError(
                f"SUMO adapter cannot advance by dt={dt}. It must be a multiple of the timestep length {self.timestep_length}."
            )

        steps = int(dt / self.timestep_length)
        for _ in range(steps):
            self._traci.simulationStep()
            self._subscribe_to_new_vehicles()
            self._store_vehicle_positions()
            self._model_time += self.timestep_length

    def _store_vehicle_positions(self):
        # Read subscriptions and cache the last edge.
        for vid in self._traci.vehicle.getIDList():
            values = self._traci.vehicle.getSubscriptionResults(vid)
            road_id = values.get(VAR_ROAD_ID) if values else None
            if road_id:
                self._last_edge_by_vid[vid] = road_id

    def _subscribe_to_new_vehicles(self):
        for vid in self._traci.simulation.getDepartedIDList():
            self._traci.vehicle.subscribe(vid, (VAR_ROAD_ID,))

    def terminate(self):
        if self._traci is not None:
            self._traci.close()
            self._traci = None
