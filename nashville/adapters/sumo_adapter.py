from adapter import Adapter
from nashville.inputs.sumo import vehicle_charge
from nashville.outputs.sumo import ArrivedEV, DepartedEV, VehicleBattery
import libsumo
import math
import warnings
from libsumo import constants as traci_constants
from datetime import timedelta
from typing import Literal


class SumoAdapter(Adapter):
    input_types = [vehicle_charge]
    output_types = [ArrivedEV, DepartedEV, VehicleBattery]
    constant_types = []

    def __init__(self, name, timestep_length, sumo_config):
        super().__init__(name=name, timestep_length=timestep_length)
        self.sumo_config = sumo_config
        self.batteries_stats = {}
        self.evs_awaiting_charge = {}
        self.phev_controller = PHEVController()

    def initialize(self):
        libsumo.start(
            [
                "sumo",
                "-c",
                self.sumo_config,
                "--step-length",
                str(self.timestep_length.total_seconds()),
                "--keep-after-arrival",
                "1",
            ]
        )
        self.sumo_start_time = libsumo.simulation.getTime()

    def current_time(self):
        return timedelta(seconds=libsumo.simulation.getTime() - self.sumo_start_time)

    def write_inputs(self, inputs: dict[str, list[dict]]):
        drivers_awaiting_charge = self.evs_awaiting_charge.keys()
        for row in inputs.get(vehicle_charge.name, []):
            driver_id = row["driver_id"]
            if driver_id in drivers_awaiting_charge:
                veh_id = self.evs_awaiting_charge[driver_id]
                self._set_charge(veh_id, row["final_charge_wh"])
                self.evs_awaiting_charge.pop(driver_id)

    def _set_charge(self, veh_id: str, final_charge_wh: float):
        current_charge = self._battery_charge_wh(veh_id)
        if current_charge is None:
            return
        stats = self.batteries_stats[veh_id]
        used_since_departure = stats["departure_charge_wh"] - current_charge
        new_charge = min(
            max(final_charge_wh - used_since_departure, 0.0), stats["capacity_wh"]
        )
        libsumo.vehicle.setParameter(
            veh_id, "device.battery.actualBatteryCapacity", str(new_charge)
        )

    def advance(self):
        libsumo.simulationStep()
        for departed_veh_id in libsumo.simulation.getDepartedIDList():
            veh_type = self._veh_type(departed_veh_id)
            if veh_type in ["EV_car_std", "EV_car_long", "PHEV_car_std"]:
                self.batteries_stats[departed_veh_id] = {
                    "veh_type": veh_type,
                    "capacity_wh": self._battery_capacity_wh(departed_veh_id),
                    "departure_charge_wh": self._battery_charge_wh(departed_veh_id),
                }
                self.evs_awaiting_charge[
                    libsumo.vehicle.getParameter(departed_veh_id, "driver_id")
                ] = departed_veh_id
            if veh_type == "PHEV_car_std":
                self.phev_controller.add_phev(departed_veh_id, veh_type)
        self.phev_controller.update_phev_soc(self.current_time())

    def _veh_type(self, veh_id: str) -> str | None:
        if veh_id in self.batteries_stats:
            return self.batteries_stats[veh_id]["veh_type"]
        try:
            return libsumo.vehicle.getTypeID(veh_id)
        except libsumo.TraCIException:
            return None

    def _battery_capacity_wh(self, veh_id: str) -> float | None:
        try:
            return float(
                libsumo.vehicle.getParameter(veh_id, "device.battery.capacity")
            )
        except libsumo.TraCIException:
            return None

    def _battery_charge_wh(self, veh_id: str) -> float | None:
        try:
            return float(
                libsumo.vehicle.getParameter(
                    veh_id, "device.battery.actualBatteryCapacity"
                )
            )
        except libsumo.TraCIException:
            return None

    def read_outputs(self) -> list[ArrivedEV | DepartedEV | VehicleBattery]:
        outputs = []
        outputs += self._get_ev_batteries(libsumo.simulation.getDepartedIDList())
        outputs += self._get_arrived_evs()
        outputs += self._get_departed_evs()
        return outputs

    def _get_ev_batteries(self, departed_veh_ids: list[str]) -> list[VehicleBattery]:
        batteries = []
        for veh_id in departed_veh_ids:
            capacity = self._capacity_wh(veh_id)
            charging_power = self._charging_power_w(veh_id)
            if capacity and charging_power:
                batteries.append(
                    VehicleBattery(
                        veh_id=veh_id,
                        capacity_wh=capacity,
                        charging_power_w=charging_power,
                    )
                )
        return batteries

    def _charging_power_w(self, veh_id: str) -> float | None:
        try:
            return float(
                libsumo.vehicle.getParameter(veh_id, "device.battery.maximumChargeRate")
            )
        except libsumo.TraCIException:
            return None

    def _get_arrived_evs(self) -> list[ArrivedEV]:
        arrived_evs = []
        arrived_ev_ids = self._get_ev_ids(state="arrived")
        current_time = self.current_time()
        for veh_id in arrived_ev_ids:
            driver_id = self._driver_id(veh_id)
            charge = self._battery_charge_wh(veh_id)
            coords = self._arrival_coords(veh_id)
            if charge is None or coords is None:
                warnings.warn(
                    f"arrived EV '{veh_id}' without charge or arrival position; "
                    "skipped",
                    RuntimeWarning,
                )
            else:
                arrived_evs.append(
                    ArrivedEV(
                        veh_id=veh_id,
                        driver_id=driver_id,
                        charge_wh=charge,
                        veh_type=self._veh_type(veh_id),
                        time_s=current_time,
                        coords=coords,
                    )
                )
            self.batteries_stats.pop(veh_id, None)
            self.evs_awaiting_charge.pop(driver_id, None)
            self.phev_controller.remove_phev(veh_id)
        return arrived_evs

    def _get_ev_ids(self, state: Literal["arrived", "departed"]) -> list[str]:
        if state == "arrived":
            veh_ids = libsumo.simulation.getArrivedIDList()
        elif state == "departed":
            veh_ids = libsumo.simulation.getDepartedIDList()
        else:
            raise ValueError(f"state must be 'arrived' or 'departed', not {state}")
        return [veh_id for veh_id in veh_ids if veh_id in self.batteries_stats]

    def _get_departed_evs(self) -> list[DepartedEV]:
        departed_evs = []
        departed_ev_ids = self._get_ev_ids(state="departed")
        current_time = self.current_time()
        for veh_id in departed_ev_ids:
            charge = self._battery_charge_wh(veh_id)
            if charge is not None:
                departed_evs.append(
                    DepartedEV(
                        veh_id=veh_id,
                        driver_id=self._driver_id(veh_id),
                        charge_wh=charge,
                        time_s=current_time,
                    )
                )
        return departed_evs

    def _driver_id(self, veh_id: str) -> str | None:
        try:
            return libsumo.vehicle.getParameter(veh_id, "driver_id")
        except libsumo.TraCIException:
            return None

    def _capacity_wh(self, veh_id: str) -> float | None:
        battery = self.batteries_stats.get(veh_id)
        return battery["capacity_wh"] if battery else None

    def _arrival_coords(self, veh_id: str) -> tuple[float, float] | None:
        try:
            last_edge = libsumo.vehicle.getRoute(veh_id)[-1]
            arrival_pos = libsumo.lane.getLength(f"{last_edge}_0")
            return libsumo.simulation.convert2D(last_edge, arrival_pos, toGeo=True)
        except libsumo.TraCIException:
            return None

    def terminate(self):
        libsumo.close()


class PHEVController:
    _SUMO_ENERGY_DEFAULTS = {
        "frontSurfaceArea": 2.6,
        "airDragCoefficient": 0.35,
        "rollDragCoefficient": 0.01,
        "constantPowerIntake": 100.0,
        "propulsionEfficiency": 0.98,
        "recuperationEfficiency": 0.96,
        "rotatingMass": 40.0,
    }
    _G = 9.81
    _AIR_DENSITY = 1.2

    def __init__(
        self,
        ev_power_limit_kw: float = 60,
        reserve_soc: float = 0.2,
        ice_duration: timedelta = timedelta(seconds=30),
    ):
        self.ev_power_limit_kw = ev_power_limit_kw
        self.reserve_soc = reserve_soc
        self.ice_duration = ice_duration
        self.veh_types = {}
        self.phevs = {}

    def _energy_param(self, veh_type: str, key: str) -> float:
        try:
            param = libsumo.vehicletype.getParameter(veh_type, key)
        except libsumo.TraCIException:
            param = ""
        return float(param) if param else self._SUMO_ENERGY_DEFAULTS[key]

    def _store_veh_type(self, veh_type: str):
        params = self.veh_types.get(veh_type)
        if params is None:
            params = {
                key: self._energy_param(veh_type, key)
                for key in self._SUMO_ENERGY_DEFAULTS
            }
            params["mass"] = libsumo.vehicletype.getMass(veh_type)
            self.veh_types[veh_type] = params

    def add_phev(self, veh_id: str, veh_type: str):
        self._store_veh_type(veh_type)
        self._subscribe_to_phev(veh_id)
        self.phevs[veh_id] = {
            "veh_type": veh_type,
            "capacity_wh": float(
                libsumo.vehicle.getParameter(veh_id, "device.battery.capacity")
            ),
            "current_mode": "EV",
            "last_ice_activation": None,
        }

    def _subscribe_to_phev(self, veh_id: str):
        var = [
            traci_constants.VAR_PARAMETER,
            traci_constants.VAR_SPEED,
            traci_constants.VAR_ACCELERATION,
        ]
        parameters = {
            traci_constants.VAR_PARAMETER: "device.battery.actualBatteryCapacity"
        }
        libsumo.vehicle.subscribe(veh_id, var, parameters=parameters)

    def update_phev_soc(self, current_time: timedelta):
        results = self._get_subscription_results()
        step_s = libsumo.simulation.getDeltaT()
        for veh_id, result in results.items():
            # SUMO's Energy model uses the mean speed over the step, not the
            # speed at its end.
            mean_speed = result["speed"] - result["accel"] * step_s / 2
            power_kw = self._compute_power_kw(
                self.phevs[veh_id]["veh_type"],
                mean_speed,
                result["accel"],
            )
            if self.phevs[veh_id]["current_mode"] == "ICE" and power_kw < 0:
                self._recuperate_energy(veh_id, -power_kw, result["charge_wh"])
            new_mode = self._determine_mode(
                power_kw, result["charge_wh"], veh_id, current_time
            )
            self._switch_emission_class(
                veh_id, self.phevs[veh_id]["current_mode"], new_mode
            )

    def _recuperate_energy(self, veh_id: str, power_kw: float, charge_wh: float):
        capacity_wh = self.phevs[veh_id]["capacity_wh"]
        step_h = libsumo.simulation.getDeltaT() / 3600
        new_charge_wh = min(charge_wh + power_kw * 1000 * step_h, capacity_wh)
        libsumo.vehicle.setParameter(
            veh_id, "device.battery.actualBatteryCapacity", str(new_charge_wh)
        )

    def _get_subscription_results(self) -> dict[str, dict[str, float]]:
        results = {}
        for veh_id, res in libsumo.vehicle.getAllSubscriptionResults().items():
            if (
                traci_constants.VAR_SPEED not in res
                or traci_constants.VAR_PARAMETER not in res
            ):
                continue
            results[veh_id] = {
                "speed": float(res[traci_constants.VAR_SPEED]),
                "accel": float(res[traci_constants.VAR_ACCELERATION]),
                "charge_wh": float(res[traci_constants.VAR_PARAMETER]),
            }
        return results

    def _determine_mode(
        self, power_kw: float, charge_wh: float, veh_id: str, current_time: timedelta
    ):
        """PHEVs drive in ICE mode when their SOC is below the reserve level
        (reserve_soc), when they need more power than the electric motor can
        provide (ev_power_limit_kw), or when the ICE was activated within the
        last ice_duration (last_ice_activation). Otherwise, they drive in EV
        mode."""
        soc = charge_wh / self.phevs[veh_id]["capacity_wh"]
        if soc < self.reserve_soc or power_kw >= self.ev_power_limit_kw:
            self.phevs[veh_id]["last_ice_activation"] = current_time
            return "ICE"
        last_ice_activation = self.phevs[veh_id]["last_ice_activation"]
        if (
            last_ice_activation is not None
            and current_time < last_ice_activation + self.ice_duration
        ):
            return "ICE"
        return "EV"

    def _switch_emission_class(self, veh_id: str, current_mode: str, new_mode: str):
        if current_mode == new_mode:
            return
        if new_mode == "ICE":
            libsumo.vehicle.setEmissionClass(veh_id, "HBEFA4/PC_petrol_Euro-6ab")
        else:
            libsumo.vehicle.setEmissionClass(veh_id, "Energy/unknown")
        self.phevs[veh_id]["current_mode"] = new_mode

    def _compute_power_kw(
        self,
        veh_type: str,
        speed: float,
        accel: float,
        slope_deg: float = 0.0,
    ) -> float:
        params = self.veh_types[veh_type]
        mass = params["mass"]
        slope = math.radians(slope_deg)

        inertia_n = (mass + params["rotatingMass"]) * accel
        gravity_n = mass * self._G * math.sin(slope)
        rolling_n = mass * self._G * params["rollDragCoefficient"] * math.cos(slope)
        drag_area = params["airDragCoefficient"] * params["frontSurfaceArea"]
        drag_n = 0.5 * self._AIR_DENSITY * drag_area * speed**2

        power_w = (inertia_n + gravity_n + rolling_n + drag_n) * speed
        power_w += params["constantPowerIntake"]
        if power_w > 0:
            power_w /= params["propulsionEfficiency"]
        else:
            power_w *= params["recuperationEfficiency"]
        return power_w / 1000

    def remove_phev(self, veh_id: str):
        self.phevs.pop(veh_id, None)
