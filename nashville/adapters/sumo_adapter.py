import os
from adapter import Adapter
from nashville.inputs.sumo import vehicle_soc
from nashville.outputs.sumo import EV, VehicleBattery
import traci
import traci.constants as tc


class SumoAdapter(Adapter):
    input_types = vehicle_soc
    output_types = [EV, VehicleBattery]
    _EV_SPEED_THRESHOLD_MPS = 15 / 3.6
    _ICE_SPEED_THRESHOLD_MPS = 30 / 3.6

    def __init__(self, name, timestep_length, sumo_config):
        super().__init__(
            name=name,
            timestep_length=timestep_length,
        )
        self._sumo_config = sumo_config
        self._traci = None
        self._phev_vehicles = {}

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

    def _get_fuel_type(self, veh_id: str) -> str:
        if "PHEV" in veh_id:
            return "PHEV"
        elif "EV" in veh_id:
            return "EV"
        return "ICE"

    def _get_veh_class(self, veh_id: str) -> str:
        try:
            type_id = traci.vehicle.getTypeID(veh_id)
            return traci.vehicletype.getVehicleClass(type_id)
        except traci.exceptions.TraCIException:
            return None

    def _get_arrived_vehicles(self) -> list[EV]:
        evs = []
        for veh_id in self._traci.simulation.getArrivedIDList():
            soc = self._get_soc(veh_id)
            if soc is not None:
                tour_id = self._get_tour_id(veh_id)
                fuel_type = self._get_fuel_type(veh_id)
                veh_class = self._get_veh_class(veh_id)
                coords = self._get_vehicle_coords(veh_id)
                lon, lat = coords
                evs.append(
                    EV(
                        veh_id=veh_id,
                        tour_id=tour_id,
                        soc=soc,
                        state="arrived",
                        fuel_type=fuel_type,
                        veh_class=veh_class,
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
            if soc is not None:
                tour_id = self._get_tour_id(veh_id)
                fuel_type = self._get_fuel_type(veh_id)
                veh_class = self._get_veh_class(veh_id)
                coords = self._get_vehicle_coords(veh_id)
                lon, lat = coords
                evs.append(
                    EV(
                        veh_id=veh_id,
                        tour_id=tour_id,
                        soc=soc,
                        state="departed",
                        fuel_type=fuel_type,
                        veh_class=veh_class,
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
        self.update_phev()
        self.model_time += self.timestep_length
        return self.model_time

    def _subscribe_phev(self):
        for veh_id in self._traci.simulation.getDepartedIDList():
            if "phev" in veh_id:
                self._traci.vehicle.subscribe(
                    veh_id,
                    [tc.VAR_SPEED, tc.VAR_PARAMETER],
                    parameters={
                        tc.VAR_PARAMETER: "device.battery.actualBatteryCapacity"
                    },
                )

    def update_phev(self):
        self._subscribe_phev()
        results = self._traci.vehicle.getAllSubscriptionResults()
        for veh_id, result in results.items():
            speed = float(result[tc.VAR_SPEED])
            soc = float(result[tc.VAR_PARAMETER])
            if veh_id not in self._phev_vehicles:
                mode = "EV" if speed <= self._EV_SPEED_THRESHOLD_MPS else "ICE"
                self._phev_vehicles[veh_id] = {
                    "speed": speed,
                    "soc": soc,
                    "mode": mode,
                }
                self._switch_emissions(veh_id, mode)
            else:
                self._phev_vehicles[veh_id]["speed"] = speed
                self._phev_vehicles[veh_id]["soc"] = self._update_soc(
                    veh_id,
                    self._phev_vehicles[veh_id],
                    soc,
                    speed,
                )

    def _update_soc(self, veh_id, state, raw_soc, speed):
        current_soc = state["soc"]

        if current_soc > 0:
            if state["mode"] == "EV" and speed > self._ICE_SPEED_THRESHOLD_MPS:
                state["mode"] = "ICE"
            elif state["mode"] == "ICE" and speed < self._EV_SPEED_THRESHOLD_MPS:
                state["mode"] = "EV"

        if current_soc <= 0:
            state["mode"] = "ICE"
            self._switch_emissions(veh_id, "ICE")
            self._traci.vehicle.setParameter(
                veh_id, "device.battery.actualBatteryCapacity", str(current_soc)
            )
            return current_soc

        if state["mode"] == "ICE":
            self._switch_emissions(veh_id, "ICE")
            delta = raw_soc - current_soc
            if self._EV_SPEED_THRESHOLD_MPS <= speed <= self._ICE_SPEED_THRESHOLD_MPS:
                credited_soc = current_soc + delta * self._ASSIST_FRACTION
            else:
                credited_soc = current_soc
            self._traci.vehicle.setParameter(
                veh_id, "device.battery.actualBatteryCapacity", str(credited_soc)
            )
            return credited_soc

        self._switch_emissions(veh_id, "EV")
        return raw_soc

    def _switch_emissions(self, veh_id, mode):
        if mode == "ICE":
            self._traci.vehicle.setEmissionClass(veh_id, "HBEFA4/PC_petrol_Euro-6ab")
        elif mode == "EV":
            self._traci.vehicle.setEmissionClass(veh_id, "Energy/unknown")

    def terminate(self):
        if self._traci is not None:
            self._traci.close()
            self._traci = None
