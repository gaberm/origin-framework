import os

from adapter import Adapter
from nashville.inputs.sumo import vehicle_soc
from nashville.outputs.sumo import EV, VehicleBattery
import traci


class SumoAdapter(Adapter):
    input_types = vehicle_soc
    output_types = [EV, VehicleBattery]

    def __init__(self, name, timestep_length, sumo_config):
        super().__init__(
            name=name,
            timestep_length=timestep_length,
        )
        self._sumo_config = sumo_config
        self._traci = None

    def initialize(self):
        traci.start(
            [
                "sumo",
                "-c",
                self._sumo_config,
                "--no-warnings",
                "--error-log",
                os.devnull,
            ]
        )
        self._traci = traci
        self._timestep_length = self._traci.simulation.getDeltaT()

    def read_outputs(self) -> list[EV]:
        output = []
        output += self._get_arrived_vehicles()
        output += self._get_departed_vehicles()
        output += self._get_battery()
        return output

    def _get_vehicle_coords(self, veh_id: str) -> tuple[float, float] | None:
        try:
            x, y = self._traci.vehicle.getPosition(veh_id)
        except traci.exceptions.TraCIException:
            return None
        if traci.constants.INVALID_DOUBLE_VALUE in (x, y):
            return None
        return self._traci.simulation.convertGeo(x, y)

    def _get_soc(self, veh_id: str) -> float:
        try:
            energy_consumed = self._traci.vehicle.getParameter(
                veh_id, "device.battery.totalEnergyConsumed"
            )
            energy_capacity = self._traci.vehicle.getParameter(
                veh_id, "device.battery.capacity"
            )
            return float(energy_consumed) / float(energy_capacity)
        except traci.exceptions.TraCIException:
            return None

    def _get_tour_id(self, veh_id: str) -> int:
        try:
            return int(self._traci.vehicle.getParameter(veh_id, "tour_id"))
        except (traci.exceptions.TraCIException, ValueError):
            return None

    def _get_arrived_vehicles(self) -> list[EV]:
        evs = []
        for veh_id in self._traci.simulation.getArrivedIDList():
            soc = self._get_soc(veh_id)
            coords = self._get_vehicle_coords(veh_id)
            if soc is not None and coords is not None:
                lon, lat = coords
                tour_id = self._get_tour_id(veh_id)
                evs.append(
                    EV(
                        veh_id=veh_id,
                        tour_id=tour_id,
                        soc=soc,
                        state="arrived",
                        time=self.model_time,
                        coords=[lon, lat],
                    )
                )
        return evs

    def _get_departed_vehicles(self) -> list[EV]:
        evs = []
        known_vehicles = set(self._traci.vehicle.getIDList())
        for veh_id in self._traci.simulation.getDepartedIDList():
            if veh_id not in known_vehicles:
                continue
            soc = self._get_soc(veh_id)
            coords = self._get_vehicle_coords(veh_id)
            if soc is not None and coords is not None:
                lon, lat = coords
                tour_id = self._get_tour_id(veh_id)
                evs.append(
                    EV(
                        veh_id=veh_id,
                        tour_id=tour_id,
                        soc=soc,
                        state="departed",
                        time=self.model_time,
                        coords=[lon, lat],
                    )
                )
        return evs

    def _get_battery(self) -> list[VehicleBattery]:
        capacities = []
        known_vehicles = set(self._traci.vehicle.getIDList())
        for veh_id in self._traci.simulation.getDepartedIDList():
            if veh_id not in known_vehicles:
                continue
            try:
                capacity = self._traci.vehicle.getParameter(
                    veh_id, "device.battery.capacity"
                )
            except traci.exceptions.TraCIException:
                continue
            try:
                max_charge_rate = self._traci.vehicle.getParameter(
                    veh_id, "device.battery.maximumChargeRate"
                )
            except traci.exceptions.TraCIException:
                continue
            capacities.append(
                VehicleBattery(
                    veh_id=veh_id,
                    capacity=capacity,
                    charging_power=max_charge_rate,
                )
            )
        return capacities

    def write_inputs(self, inputs: dict[str, list[dict]]):
        for veh_id, soc in inputs.get(vehicle_soc.name, []):
            self._traci.vehicle.setParameter(
                veh_id, "device.battery.actualBatteryCapacity", str(soc * 100)
            )

    def advance(self) -> float:
        self._traci.simulationStep()
        self.model_time += self.timestep_length
        return self.model_time

    def terminate(self):
        if self._traci is not None:
            self._traci.close()
            self._traci = None
