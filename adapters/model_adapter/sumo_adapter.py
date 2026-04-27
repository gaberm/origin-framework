from adapters import ModelAdapter
from records import (
    ArrivedVehicleRecord,
    DepartedVehicleRecord,
    ModelOutput,
    VehicleStateRecord,
)
from models.inputs import TransportationInputs
from shapely.geometry import Point
import traci
from traci.constants import VAR_ROAD_ID


class TransportationAdapter(ModelAdapter):
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

    def read_outputs(self) -> ModelOutput:
        output = ModelOutput()
        output.add_many(self._get_arrived_vehicles())
        output.add_many(self._get_departed_vehicles())
        output.add_many(self._get_vehicle_states())
        return output

    def _vehicle_point(self, v_id: str) -> Point:
        lon, lat = self._traci.simulation.convertGeo(
            *self._traci.vehicle.getPosition(v_id)
        )
        return Point(lon, lat)

    def _get_arrived_vehicles(self) -> tuple[ArrivedVehicleRecord, ...]:
        return tuple(
            ArrivedVehicleRecord(
                global_time=self.model_time,
                geometry=self._vehicle_point(v_id),
                vehicle_id=v_id,
                road_id=self._last_edge_by_vid.get(v_id, "unknown"),
                soc_at_arrival=0.0,
            )
            for v_id in self._traci.simulation.getArrivedIDList()
        )

    def _get_departed_vehicles(self) -> tuple[DepartedVehicleRecord, ...]:
        return tuple(
            DepartedVehicleRecord(
                global_time=self.model_time,
                geometry=self._vehicle_point(v_id),
                vehicle_id=v_id,
                road_id=self._last_edge_by_vid.get(v_id, "unknown"),
            )
            for v_id in self._traci.simulation.getDepartedIDList()
        )

    def _get_vehicle_states(self) -> tuple[VehicleStateRecord, ...]:
        return tuple(
            VehicleStateRecord(
                global_time=self.model_time,
                geometry=self._vehicle_point(v_id),
                vehicle_id=v_id,
                road_id=self._last_edge_by_vid.get(v_id, "unknown"),
                speed=self._traci.vehicle.getSpeed(v_id),
            )
            for v_id in self._traci.vehicle.getIDList()
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
