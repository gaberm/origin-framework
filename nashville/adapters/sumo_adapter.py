import gzip
import os
import warnings
from datetime import timedelta
import xml.etree.ElementTree as ET
import pyproj
from adapter import Adapter
from nashville.inputs.sumo import vehicle_soc
from nashville.outputs.sumo import EV, VehicleBattery

try:
    import libsumo as api
except ImportError:
    import traci as api
tc = api.constants


class SumoAdapter(Adapter):
    input_types = vehicle_soc
    output_types = [EV, VehicleBattery]
    _EV_SPEED_THRESHOLD_MPS = 15 / 3.6
    _ICE_SPEED_THRESHOLD_MPS = 30 / 3.6
    _ASSIST_FRACTION = 0.5

    def __init__(self, name, timestep_length, sumo_config):
        super().__init__(
            name=name,
            timestep_length=timestep_length,
        )
        self._sumo_config = sumo_config
        self._api = None
        self._begin_s = None  # SUMO's clock at the start of the run
        self._net_offset = None
        self._proj = None
        self._departed_ids = []  # in the latest step
        self._static_veh_attrs = {}
        self._dynamic_veh_attrs = {}
        self._subscription_results = {}
        self._vehicle_by_driver = {}  # driver_id -> battery vehicle on the road

    def initialize(self):
        api.start(
            [
                "sumo",
                "-c",
                self._sumo_config,
                # The spec, not the config, sets the step length; the command
                # line overrides the config's value.
                "--step-length",
                str(self.timestep_length.total_seconds()),
                # "--no-warnings",
                "--error-log",
                os.devnull,
            ]
        )
        self._api = api
        self._begin_s = self._api.simulation.getTime()
        self._net_offset, self._proj = self._read_projection(self._resolve_net_file())

    def current_time(self) -> timedelta:
        return timedelta(seconds=self._api.simulation.getTime() - self._begin_s)

    def _resolve_net_file(self) -> str:
        config_dir = os.path.dirname(self._sumo_config)
        net_file = ET.parse(self._sumo_config).find(".//net-file").get("value")
        return os.path.join(config_dir, net_file)

    @staticmethod
    def _read_projection(net_file: str) -> tuple[tuple[float, float], pyproj.Proj]:
        """The network's offset and projection, as sumolib's convertXY2LonLat
        uses them. Only <location>, at the top of the file, is read: loading
        the whole network with sumolib is slow and memory-hungry."""
        opener = gzip.open if net_file.endswith(".gz") else open
        with opener(net_file, "rb") as file:
            for _, element in ET.iterparse(file, events=("start",)):
                if element.tag == "location":
                    proj_parameter = element.get("projParameter")
                    if proj_parameter == "!":
                        raise ValueError(
                            f"{net_file} has no geographic projection "
                            "(projParameter='!')"
                        )
                    offset_x, offset_y = element.get("netOffset").split(",")
                    offset = (float(offset_x), float(offset_y))
                    return offset, pyproj.Proj(proj_parameter)
        raise ValueError(f"{net_file} has no <location> element")

    def _to_lon_lat(self, x: float, y: float) -> tuple[float, float]:
        offset_x, offset_y = self._net_offset
        return self._proj(x - offset_x, y - offset_y, inverse=True)

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
        if api.constants.INVALID_DOUBLE_VALUE in (x, y):
            return None
        return self._to_lon_lat(x, y)

    def _get_soc(self, veh_id: str) -> float:
        dynamic_attrs = self._dynamic_veh_attrs.get(veh_id)
        static_attrs = self._static_veh_attrs.get(veh_id)
        if (
            dynamic_attrs is None
            or static_attrs is None
            or not static_attrs["capacity_wh"]
        ):
            return None
        try:
            # SUMO's actualBatteryCapacity is the charge left in Wh, despite
            # its name, so this is the state of charge.
            return float(dynamic_attrs["battery_charge"]) / float(
                static_attrs["capacity_wh"]
            )
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def _get_tour_id(self, veh_id: str) -> int:
        static_attrs = self._static_veh_attrs.get(veh_id)
        return static_attrs["tour_id"] if static_attrs else None

    def _get_driver_id(self, veh_id: str) -> str:
        static_attrs = self._static_veh_attrs.get(veh_id)
        return static_attrs["driver_id"] if static_attrs else veh_id

    def _battery_charge(self, veh_id: str) -> float | None:
        try:
            return float(self._dynamic_veh_attrs[veh_id]["battery_charge"])
        except (KeyError, TypeError, ValueError):
            return None

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
        time = self.current_time()
        for veh_id in self._api.simulation.getArrivedIDList():
            soc = self._get_soc(veh_id)
            if soc is not None:
                tour_id = self._get_tour_id(veh_id)
                driver_id = self._get_driver_id(veh_id)
                fuel_type = self._get_fuel_type(veh_id)
                veh_class = self._get_veh_class(veh_id)
                coords = self._get_vehicle_coords(veh_id)
                if coords is None:
                    warnings.warn(
                        f"arrived vehicle '{veh_id}' has no valid position; skipped",
                        RuntimeWarning,
                    )
                else:
                    lon, lat = coords
                    evs.append(
                        EV(
                            veh_id=veh_id,
                            tour_id=tour_id,
                            driver_id=driver_id,
                            soc=soc,
                            state="arrived",
                            fuel_type=fuel_type,
                            veh_class=veh_class,
                            time_s=time,
                            coords=[lon, lat],
                        )
                    )
            self._forget_vehicle(veh_id)
        return evs

    def _get_departed_evs(self) -> list[EV]:
        evs = []
        time = self.current_time()
        for veh_id in self._departed_ids:
            soc = self._get_soc(veh_id)
            if soc is not None:
                tour_id = self._get_tour_id(veh_id)
                driver_id = self._get_driver_id(veh_id)
                self._vehicle_by_driver[driver_id] = veh_id
                fuel_type = self._get_fuel_type(veh_id)
                veh_class = self._get_veh_class(veh_id)
                coords = self._get_vehicle_coords(veh_id)
                if coords is None:
                    warnings.warn(
                        f"departed vehicle '{veh_id}' has no valid position; skipped",
                        RuntimeWarning,
                    )
                    continue
                lon, lat = coords
                evs.append(
                    EV(
                        veh_id=veh_id,
                        tour_id=tour_id,
                        driver_id=driver_id,
                        soc=soc,
                        state="departed",
                        fuel_type=fuel_type,
                        veh_class=veh_class,
                        time_s=time,
                        coords=[lon, lat],
                    )
                )
        return evs

    def _get_battery(self) -> list[VehicleBattery]:
        capacities = []
        for veh_id in self._departed_ids:
            static_attrs = self._static_veh_attrs.get(veh_id)
            if (
                static_attrs is None
                or static_attrs["capacity_wh"] is None
                or static_attrs["charging_power_w"] is None
            ):
                continue
            capacities.append(
                VehicleBattery(
                    veh_id=veh_id,
                    capacity_wh=static_attrs["capacity_wh"],
                    charging_power_w=static_attrs["charging_power_w"],
                )
            )
        return capacities

    def write_inputs(self, inputs: dict[str, list[dict]]):
        for row in inputs.get(vehicle_soc.name, []):
            self._apply_charging_result(row["driver_id"], row["final_soc"])

    def _apply_charging_result(self, driver_id: str, final_soc: float):
        """Give the driver's vehicle on the road the charge its last charging
        session ended with. The result arrives after the vehicle departed, so
        the charge is shifted rather than overwritten, keeping the energy used
        since departure."""
        veh_id = self._vehicle_by_driver.get(driver_id)
        static_attrs = self._static_veh_attrs.get(veh_id)
        if (
            static_attrs is None
            or not static_attrs["capacity_wh"]
            or static_attrs["departure_charge_wh"] is None
        ):
            # E.g. a trip shorter than the charging step: the vehicle already
            # arrived. The message leaves out ids so it prints only once.
            warnings.warn(
                "charging result for a driver with no vehicle on the road; skipped",
                RuntimeWarning,
            )
            return
        capacity_wh = static_attrs["capacity_wh"]
        delta_wh = final_soc * capacity_wh - static_attrs["departure_charge_wh"]
        try:
            charge_wh = float(
                self._api.vehicle.getParameter(
                    veh_id, "device.battery.actualBatteryCapacity"
                )
            )
            new_charge_wh = min(max(charge_wh + delta_wh, 0.0), capacity_wh)
            self._api.vehicle.setParameter(
                veh_id, "device.battery.actualBatteryCapacity", str(new_charge_wh)
            )
        except (api.exceptions.apiException, ValueError) as error:
            warnings.warn(f"could not apply charging result: {error}", RuntimeWarning)
            return
        static_attrs["departure_charge_wh"] = final_soc * capacity_wh
        dynamic_attrs = self._dynamic_veh_attrs.get(veh_id, {})
        if "battery_charge" in dynamic_attrs:
            dynamic_attrs["battery_charge"] = new_charge_wh
        if "phev_soc" in dynamic_attrs:
            # The PHEV logic writes its tracked charge back to SUMO, so it has
            # to see the change as well.
            dynamic_attrs["phev_soc"] = min(
                max(dynamic_attrs["phev_soc"] + delta_wh, 0.0), capacity_wh
            )

    def advance(self):
        self._api.simulationStep()
        self._departed_ids = self._api.simulation.getDepartedIDList()
        battery_vehicles = self._subscribe_new_departures()
        self._subscription_results = self._api.vehicle.getAllSubscriptionResults()
        self._update_dynamic_veh_attrs()
        # After the update: the departure charge comes from the subscription.
        for veh_id, veh_type in battery_vehicles:
            self._save_static_attrs(veh_id, veh_type)
        self._update_phev()

    def _save_static_attrs(self, veh_id: str, veh_type: str):
        if veh_id in self._static_veh_attrs:
            return
        self._static_veh_attrs[veh_id] = {
            "capacity_wh": self._read_capacity(veh_id),
            "charging_power_w": self._read_max_charge_rate(veh_id),
            "tour_id": self._read_tour_id(veh_id),
            "driver_id": self._read_driver_id(veh_id),
            "departure_charge_wh": self._battery_charge(veh_id),
            "veh_class": self._read_veh_class(veh_id),
            "veh_type": veh_type,
        }

    def _read_capacity(self, veh_id: str) -> float | None:
        try:
            capacity = self._api.vehicle.getParameter(veh_id, "device.battery.capacity")
            return float(capacity) if capacity else None
        except api.exceptions.apiException:
            return None

    def _read_max_charge_rate(self, veh_id: str) -> float | None:
        try:
            max_charge_rate = self._api.vehicle.getParameter(
                veh_id, "device.battery.maximumChargeRate"
            )
            return float(max_charge_rate) if max_charge_rate else None
        except api.exceptions.apiException:
            return None

    def _read_tour_id(self, veh_id: str) -> int | None:
        try:
            return int(self._api.vehicle.getParameter(veh_id, "tour_id"))
        except (api.exceptions.apiException, ValueError):
            return None

    def _read_driver_id(self, veh_id: str) -> str:
        # Trucks have no household and person, so their vehicle id stands in.
        try:
            return self._api.vehicle.getParameter(veh_id, "driver_id") or veh_id
        except api.exceptions.apiException:
            return veh_id

    def _read_veh_class(self, veh_id: str) -> str | None:
        try:
            return self._api.vehicle.getVehicleClass(veh_id)
        except api.exceptions.apiException:
            return None

    def _read_veh_type(self, veh_id: str) -> str | None:
        try:
            return self._api.vehicle.getTypeID(veh_id)
        except api.exceptions.apiException:
            return None

    def _forget_vehicle(self, veh_id: str):
        static_attrs = self._static_veh_attrs.pop(veh_id, None)
        self._dynamic_veh_attrs.pop(veh_id, None)
        driver_id = static_attrs["driver_id"] if static_attrs else None
        if self._vehicle_by_driver.get(driver_id) == veh_id:
            del self._vehicle_by_driver[driver_id]

    def _subscribe_new_departures(self) -> list[tuple[str, str]]:
        """Subscribe the battery vehicles among the departed ones and return
        them with their type. Vehicles without a battery cost one call."""
        battery_vehicles = []
        for veh_id in self._departed_ids:
            veh_type = self._read_veh_type(veh_id)
            fuel_type = self._classify_veh_type(veh_type)
            if fuel_type == "ICE":
                continue
            battery_vehicles.append((veh_id, veh_type))
            var_ids = [tc.VAR_POSITION, tc.VAR_PARAMETER]
            # A plain string, not an ("s", key) tuple: api sends both the
            # same way, but libsumo rejects tuples ("Unsupported variable").
            parameters = {tc.VAR_PARAMETER: "device.battery.actualBatteryCapacity"}
            if fuel_type == "PHEV":
                var_ids.append(tc.VAR_SPEED)
            try:
                self._api.vehicle.subscribe(veh_id, var_ids, parameters=parameters)
            except api.exceptions.apiException as error:
                # Without this subscription the vehicle never gets a SOC and
                # is dropped from the EV output. The message leaves out the
                # vehicle ID so an error that hits every vehicle prints once.
                warnings.warn(f"battery subscription failed: {error}", RuntimeWarning)
        return battery_vehicles

    def _update_dynamic_veh_attrs(self):
        for veh_id, result in self._subscription_results.items():
            if tc.VAR_POSITION not in result or tc.VAR_PARAMETER not in result:
                continue
            x, y = result[tc.VAR_POSITION]
            self._dynamic_veh_attrs.setdefault(veh_id, {}).update(
                {
                    "x": x,
                    "y": y,
                    "battery_charge": result[tc.VAR_PARAMETER],
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

        mode = state["phev_mode"]
        if current_soc <= 0:
            mode = "ICE"
        elif mode == "EV" and speed > self._ICE_SPEED_THRESHOLD_MPS:
            mode = "ICE"
        elif mode == "ICE" and speed < self._EV_SPEED_THRESHOLD_MPS:
            mode = "EV"
        # SUMO keeps a vehicle's emission class, so set it only on a change.
        if mode != state["phev_mode"]:
            state["phev_mode"] = mode
            self._switch_emissions(veh_id, mode)

        if current_soc <= 0:
            self._write_battery_charge(veh_id, raw_soc, current_soc)
            return current_soc

        if mode == "ICE":
            delta = raw_soc - current_soc
            if self._EV_SPEED_THRESHOLD_MPS <= speed <= self._ICE_SPEED_THRESHOLD_MPS:
                credited_soc = current_soc + delta * self._ASSIST_FRACTION
            else:
                credited_soc = current_soc
            self._write_battery_charge(veh_id, raw_soc, credited_soc)
            return credited_soc

        return raw_soc

    def _write_battery_charge(self, veh_id, sumo_charge, charge):
        # Undo SUMO's drain; skipped when there is nothing to undo.
        if sumo_charge != charge:
            self._api.vehicle.setParameter(
                veh_id, "device.battery.actualBatteryCapacity", str(charge)
            )

    def _switch_emissions(self, veh_id, mode):
        if mode == "ICE":
            self._api.vehicle.setEmissionClass(veh_id, "HBEFA4/PC_petrol_Euro-6ab")
        elif mode == "EV":
            self._api.vehicle.setEmissionClass(veh_id, "Energy/unknown")

    def terminate(self):
        if self._api is not None:
            self._api.close()
            self._api = None
