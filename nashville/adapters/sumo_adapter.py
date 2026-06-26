from typing import Mapping
from adapter import Adapter
from nashville.inputs.sumo import vehicle_soc_input
from nashville.outputs.sumo import EV, VehicleBattery
import traci


class SumoAdapter(Adapter):
    InputType = vehicle_soc_input
    OutputType = [EV, VehicleBattery]

    def __init__(self, name, timestep_length, sumo_config):
        super().__init__(
            name=name,
            timestep_length=timestep_length,
        )
        self._sumo_config = sumo_config
        self._traci = None

    def initialize(self):
        traci.start(["sumo", "-c", self._sumo_config])
        self._traci = traci
        self._timestep_length = self._traci.simulation.getDeltaT()

    def read_outputs(self) -> list[EV]:
        output = []
        output += self._get_arrived_vehicles()
        output += self._get_departed_vehicles()
        output += self._get_battery()
        return output

    def _get_vehicle_coords(self, veh_id: str) -> tuple[float, float]:
        return self._traci.simulation.convertGeo(
            *self._traci.vehicle.getPosition(veh_id)
        )

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

    def _get_arrived_vehicles(self) -> list[EV]:
        evs = []
        for veh_id in self._traci.simulation.getArrivedIDList():
            soc = self._get_soc(veh_id)
            if soc is not None:
                lon, lat = self._get_vehicle_coords(veh_id)
                evs.append(
                    EV(
                        veh_id=veh_id,
                        soc=soc,
                        state="arrived",
                        time=self.model_time,
                        coords=[lon, lat],
                    )
                )
        return evs

    def _get_departed_vehicles(self) -> list[EV]:
        evs = []
        for veh_id in self._traci.simulation.getDepartedIDList():
            soc = self._get_soc(veh_id)
            if soc is not None:
                lon, lat = self._get_vehicle_coords(veh_id)
                evs.append(
                    EV(
                        veh_id=veh_id,
                        soc=soc,
                        state="departed",
                        time=self.model_time,
                        coords=[lon, lat],
                    )
                )
        return evs

    def _get_battery(self) -> list[VehicleBattery]:
        capacities = []
        for veh_id in self._traci.simulation.getDepartedIDList():
            try:
                capacity = self._traci.vehicle.getParameter(
                    veh_id, "device.battery.capacity"
                )
            except traci.exceptions.TraCIException:
                capacity = 75_000  # 75 kWh in Wh — typical mid-range EV
            try:
                max_charge_rate = self._traci.vehicle.getParameter(
                    veh_id, "device.battery.maximumChargeRate"
                )
            except traci.exceptions.TraCIException:
                max_charge_rate = 11  # kW — standard Level 2 onboard charger
            capacities.append(
                VehicleBattery(
                    veh_id=veh_id,
                    capacity=capacity,
                    charging_power=max_charge_rate,
                )
            )
        return capacities

    def write_inputs(self, inputs: dict[str, list[dict]]):
        for veh_id, soc in inputs.get(vehicle_soc_input.name, []):
            self._traci.vehicle.setParameter(
                veh_id, "device.battery.actualBatteryCapacity", str(soc * 100)
            )

    def advance(self) -> float:
        self._traci.simulationStep()
        self._model_time += self.timestep_length
        return self._model_time

    def terminate(self):
        if self._traci is not None:
            self._traci.close()
            self._traci = None
