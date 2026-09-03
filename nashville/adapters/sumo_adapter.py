import os
import xml.etree.ElementTree as ET
import sumolib
from adapter import Adapter
from nashville.inputs.sumo import vehicle_soc
from nashville.outputs.sumo import EV, VehicleBattery

_SIF_PATH = os.environ.get("SUMO_SIF_PATH")
if _SIF_PATH:
    import traci
else:
    try:
        import libsumo as traci
    except ImportError:
        import traci
tc = traci.constants


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
        self._net = None
        self._static_veh_attrs = {}
        self._dynamic_veh_attrs = {}
        self._subscription_results = {}

    def initialize(self):
        command = ["apptainer", "exec", _SIF_PATH, "sumo"] if _SIF_PATH else ["sumo"]
        traci.start(
            command
            + [
                "-c",
                self._sumo_config,
                "--no-warnings",
                "--error-log",
                os.devnull,
            ]
        )
        self._traci = traci
        self._timestep_length = self._traci.simulation.getDeltaT()
        self._net = sumolib.net.readNet(self._resolve_net_file())

    def _resolve_net_file(self) -> str:
        config_dir = os.path.dirname(self._sumo_config)
        net_file = ET.parse(self._sumo_config).find(".//net-file").get("value")
        return os.path.join(config_dir, net_file)

    def read_outputs(self) -> list[EV]:
        output = []
        output += self._get_arrived_evs()
        output += self._get_departed_evs()
        output += self._get_battery()
        return output

    def _get_vehicle_coords(self, veh_id: str) -> tuple[float, float] | None:
        dynamic_attrs = self._dynamic_veh_attrs.get(veh_id)
        if dynamic_attrs is None:
            return None
        x, y = dynamic_attrs["x"], dynamic_attrs["y"]
        if traci.constants.INVALID_DOUBLE_VALUE in (x, y):
            return None
        return self._net.convertXY2LonLat(x, y)

    def _get_soc(self, veh_id: str) -> float:
        dynamic_attrs = self._dynamic_veh_attrs.get(veh_id)
        static_attrs = self._static_veh_attrs.get(veh_id)
        if (
            dynamic_attrs is None
            or static_attrs is None
            or not static_attrs["capacity"]
        ):
            return None
        try:
            return float(dynamic_attrs["total_energy_consumed"]) / float(
                static_attrs["capacity"]
            )
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def _get_tour_id(self, veh_id: str) -> int:
        static_attrs = self._static_veh_attrs.get(veh_id)
        return static_attrs["tour_id"] if static_attrs else None

    def _get_fuel_type(self, veh_id: str) -> str:
        static_attrs = self._static_veh_attrs.get(veh_id)
        veh_type = static_attrs["veh_type"] if static_attrs else None
        return self._classify_veh_type(veh_type)

    @staticmethod
    def _classify_veh_type(veh_type: str | None) -> str:
        veh_type = (veh_type or "").upper()
        if veh_type.startswith("PHEV"):
            return "PHEV"
        if veh_type.startswith("EV"):
            return "EV"
        return "ICE"

    def _get_veh_class(self, veh_id: str) -> str:
        static_attrs = self._static_veh_attrs.get(veh_id)
        return static_attrs["veh_class"] if static_attrs else None

    def _get_arrived_evs(self) -> list[EV]:
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
            self._forget_vehicle(veh_id)
        return evs

    def _get_departed_evs(self) -> list[EV]:
        evs = []
        for veh_id in self._traci.simulation.getDepartedIDList():
            self._save_static_attrs(veh_id)
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
        for veh_id in self._traci.simulation.getDepartedIDList():
            static_attrs = self._static_veh_attrs.get(veh_id)
            if (
                static_attrs is None
                or static_attrs["capacity"] is None
                or static_attrs["max_charge_rate"] is None
            ):
                continue
            capacities.append(
                VehicleBattery(
                    veh_id=veh_id,
                    capacity=static_attrs["capacity"],
                    charging_power=static_attrs["max_charge_rate"],
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
        self._subscribe_new_departures()
        self._subscription_results = self._traci.vehicle.getAllSubscriptionResults()
        self._update_dynamic_veh_attrs()
        self._update_phev()
        self.model_time += self.timestep_length
        return self.model_time

    def _save_static_attrs(self, veh_id: str):
        if veh_id in self._static_veh_attrs:
            return
        self._static_veh_attrs[veh_id] = {
            "capacity": self._read_capacity(veh_id),
            "max_charge_rate": self._read_max_charge_rate(veh_id),
            "tour_id": self._read_tour_id(veh_id),
            "veh_class": self._read_veh_class(veh_id),
            "veh_type": self._read_veh_type(veh_id),
        }

    def _read_capacity(self, veh_id: str) -> float | None:
        try:
            capacity = self._traci.vehicle.getParameter(
                veh_id, "device.battery.capacity"
            )
            return float(capacity) if capacity else None
        except traci.exceptions.TraCIException:
            return None

    def _read_max_charge_rate(self, veh_id: str) -> float | None:
        try:
            max_charge_rate = self._traci.vehicle.getParameter(
                veh_id, "device.battery.maximumChargeRate"
            )
            return float(max_charge_rate) if max_charge_rate else None
        except traci.exceptions.TraCIException:
            return None

    def _read_tour_id(self, veh_id: str) -> int | None:
        try:
            return int(self._traci.vehicle.getParameter(veh_id, "tour_id"))
        except (traci.exceptions.TraCIException, ValueError):
            return None

    def _read_veh_class(self, veh_id: str) -> str | None:
        try:
            return self._traci.vehicle.getVehicleClass(veh_id)
        except traci.exceptions.TraCIException:
            return None

    def _read_veh_type(self, veh_id: str) -> str | None:
        try:
            return self._traci.vehicle.getTypeID(veh_id)
        except traci.exceptions.TraCIException:
            return None

    def _forget_vehicle(self, veh_id: str):
        self._static_veh_attrs.pop(veh_id, None)
        self._dynamic_veh_attrs.pop(veh_id, None)

    def _subscribe_new_departures(self):
        for veh_id in self._traci.simulation.getDepartedIDList():
            fuel_type = self._classify_veh_type(self._read_veh_type(veh_id))
            if fuel_type == "ICE":
                continue
            var_ids = [tc.VAR_POSITION, tc.VAR_PARAMETER_WITH_KEY]
            parameters = {
                tc.VAR_PARAMETER_WITH_KEY: ("s", "device.battery.totalEnergyConsumed"),
            }
            if fuel_type == "PHEV":
                var_ids += [tc.VAR_SPEED, tc.VAR_PARAMETER]
                parameters[tc.VAR_PARAMETER] = "device.battery.actualBatteryCapacity"
            try:
                self._traci.vehicle.subscribe(veh_id, var_ids, parameters=parameters)
            except traci.exceptions.TraCIException:
                pass

    def _update_dynamic_veh_attrs(self):
        for veh_id, result in self._subscription_results.items():
            if tc.VAR_POSITION not in result or tc.VAR_PARAMETER_WITH_KEY not in result:
                continue
            x, y = result[tc.VAR_POSITION]
            _, total_energy_consumed = result[tc.VAR_PARAMETER_WITH_KEY]
            self._dynamic_veh_attrs.setdefault(veh_id, {}).update(
                {
                    "x": x,
                    "y": y,
                    "total_energy_consumed": total_energy_consumed,
                }
            )

    def _update_phev(self):
        for veh_id, result in self._subscription_results.items():
            if tc.VAR_SPEED not in result or tc.VAR_PARAMETER not in result:
                continue
            speed = float(result[tc.VAR_SPEED])
            soc = float(result[tc.VAR_PARAMETER])
            dynamic_attrs = self._dynamic_veh_attrs.setdefault(veh_id, {})
            if "phev_mode" not in dynamic_attrs:
                mode = "EV" if speed <= self._EV_SPEED_THRESHOLD_MPS else "ICE"
                dynamic_attrs["phev_speed"] = speed
                dynamic_attrs["phev_soc"] = soc
                dynamic_attrs["phev_mode"] = mode
                self._switch_emissions(veh_id, mode)
            else:
                dynamic_attrs["phev_speed"] = speed
                dynamic_attrs["phev_soc"] = self._update_soc(
                    veh_id, dynamic_attrs, soc, speed
                )

    def _update_soc(self, veh_id, state, raw_soc, speed):
        current_soc = state["phev_soc"]

        if current_soc > 0:
            if state["phev_mode"] == "EV" and speed > self._ICE_SPEED_THRESHOLD_MPS:
                state["phev_mode"] = "ICE"
            elif state["phev_mode"] == "ICE" and speed < self._EV_SPEED_THRESHOLD_MPS:
                state["phev_mode"] = "EV"

        if current_soc <= 0:
            state["phev_mode"] = "ICE"
            self._switch_emissions(veh_id, "ICE")
            self._traci.vehicle.setParameter(
                veh_id, "device.battery.actualBatteryCapacity", str(current_soc)
            )
            return current_soc

        if state["phev_mode"] == "ICE":
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
