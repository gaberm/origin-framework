from adapter import Adapter
from nashville.inputs.sumo import vehicle_charge
from nashville.outputs.sumo import (
    EV,
    Arrival,
    Departure,
    Trip,
    TripStep,
    VehicleBattery,
)
import libsumo
import math
import warnings
from libsumo import constants as traci_constants
from datetime import timedelta
from typing import Literal


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min(value, max_value), min_value)


class SumoAdapter(Adapter):
    input_types = [vehicle_charge]
    output_types = [Arrival, Departure, EV, Trip, TripStep, VehicleBattery]
    _EV_TYPES = ["EV_car_std", "EV_car_long", "PHEV_car_std", "EV_SUT", "EV_MUT"]

    def __init__(
        self,
        name: str,
        run_id: str,
        timestep_length: timedelta,
        active_from: timedelta,
        sumo_config: str,
    ):
        super().__init__(
            name=name,
            run_id=run_id,
            timestep_length=timestep_length,
            active_from=active_from,
        )
        self.sumo_config = sumo_config
        self.batteries = {}
        self.charging_tables = {}
        self.evs = {}
        self.evs_awaiting_charge = {}
        # Drivers whose EV is already written; an EV is written once.
        self.drivers = set()
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
                "--threads",
                "1",
                "--time-to-teleport",
                "120",
                "--device.rerouting.threads",
                "4",
                "--device.rerouting.period",
                "120",
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
                veh_id = self.evs_awaiting_charge[driver_id]["veh_id"]
                self._set_charge(driver_id, veh_id, row["final_charge_wh"])
                self.evs_awaiting_charge.pop(driver_id)

    def _set_charge(self, driver_id: str, veh_id: str, final_charge_wh: float):
        current_charge = self._battery_charge_wh(veh_id)
        if current_charge is None:
            return
        stats = self.evs_awaiting_charge[driver_id]
        used_since_departure = stats["departure_charge_wh"] - current_charge
        new_charge = _clamp(
            final_charge_wh - used_since_departure, 0.0, stats["capacity_wh"]
        )
        libsumo.vehicle.setParameter(
            veh_id, "device.battery.actualBatteryCapacity", str(new_charge)
        )

    def advance(self):
        libsumo.simulationStep()
        for departed_veh_id in libsumo.simulation.getDepartedIDList():
            veh_type = self._veh_type(departed_veh_id)
            if veh_type in self._EV_TYPES:
                self._store_ev(departed_veh_id, veh_type)
            if veh_type == "PHEV_car_std":
                self.phev_controller.add_phev(departed_veh_id, veh_type)
        self.phev_controller.update_phev_soc(self.current_time())

    def _veh_type(self, veh_id: str) -> str | None:
        if veh_id in self.evs:
            return self.evs[veh_id]["veh_type"]
        try:
            return libsumo.vehicle.getTypeID(veh_id)
        except libsumo.TraCIException:
            return None

    def _store_ev(self, veh_id: str, veh_type: str):
        capacity_wh = self._battery_capacity_wh(veh_id)
        driver_id = self._driver_id(veh_id)
        self.batteries[veh_id] = {
            "veh_type": veh_type,
            "driver_id": driver_id,
            "departure_s": self.current_time(),
            "capacity_wh": capacity_wh,
            "charging_tables": self._charging_tables(veh_id, veh_type),
        }
        self.evs[veh_id] = {"veh_type": veh_type, "driver_id": driver_id}
        self.evs_awaiting_charge[driver_id] = {
            "veh_id": veh_id,
            "capacity_wh": capacity_wh,
            "departure_charge_wh": self._battery_charge_wh(veh_id),
        }

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

    def read_outputs(
        self,
    ) -> list[EV | Trip | Departure | Arrival | TripStep | VehicleBattery]:
        return [
            *self._get_departures(),
            *self._get_ev_batteries(),
            *self._get_arrivals(),
            *self._get_trip_steps(),
        ]

    def _get_departures(self) -> list[EV | Trip | Departure]:
        """A Trip and its Departure per departed EV, and the driver's EV the
        first time the driver departs."""
        current_time = self.current_time()
        outputs = []
        for veh_id in self._get_ev_ids(state="departed"):
            driver_id = self._driver_id(veh_id)
            if driver_id not in self.drivers:
                self.drivers.add(driver_id)
                outputs.append(
                    EV(
                        driver_id=driver_id,
                        veh_type=self._veh_type(veh_id),
                        start_s=current_time,
                    )
                )
            outputs.append(
                Trip(veh_id=veh_id, driver_id=driver_id, start_s=current_time)
            )
            coords = self._coords(veh_id, "departed")
            if coords is None:
                warnings.warn(
                    f"departed EV '{veh_id}' without position; departure skipped",
                    RuntimeWarning,
                )
                continue
            outputs.append(Departure(veh_id=veh_id, time_s=current_time, coords=coords))
        return outputs

    def _get_ev_batteries(self) -> list[VehicleBattery]:
        batteries = []
        for veh_id, stats in self.batteries.items():
            battery = self._build_battery(veh_id, stats)
            if battery is not None:
                batteries.append(battery)
        self.batteries.clear()
        return batteries

    def _build_battery(self, veh_id: str, stats: dict) -> VehicleBattery | None:
        capacity = stats["capacity_wh"]
        tables = stats["charging_tables"]
        if not capacity or not tables:
            return None
        levels, rates = tables
        return VehicleBattery(
            veh_id=veh_id,
            driver_id=stats["driver_id"],
            start_s=stats["departure_s"],
            capacity_wh=capacity,
            charge_level_table=levels,
            charge_curve_table=rates,
        )

    def _charging_tables(
        self, veh_id: str, veh_type: str
    ) -> tuple[list[float], list[float]] | None:
        if veh_type in self.charging_tables:
            return self.charging_tables[veh_type]
        levels = self._charging_table(veh_id, "device.battery.chargeLevelTable")
        rates = self._charging_table(veh_id, "device.battery.chargeCurveTable")
        if not levels or len(levels) != len(rates):
            return None
        self.charging_tables[veh_type] = (levels, rates)
        return self.charging_tables[veh_type]

    def _charging_table(self, veh_id: str, key: str) -> list[float]:
        try:
            value = libsumo.vehicletype.getParameter(
                libsumo.vehicle.getTypeID(veh_id), key
            )
        except libsumo.TraCIException:
            return []
        return [float(number) for number in value.split()]

    def _get_arrivals(self) -> list[Arrival]:
        arrivals = []
        arrived_ev_ids = self._get_ev_ids(state="arrived")
        for veh_id in arrived_ev_ids:
            driver_id = self._driver_id(veh_id)
            arrival = self._build_arrival(veh_id)
            if arrival is not None:
                arrivals.append(arrival)
            self.evs.pop(veh_id, None)
            self.evs_awaiting_charge.pop(driver_id, None)
            self.phev_controller.remove_phev(veh_id)
        return arrivals

    def _build_arrival(self, veh_id: str) -> Arrival | None:
        charge = self._battery_charge_wh(veh_id)
        coords = self._coords(veh_id, "arrived")
        if charge is None or coords is None:
            warnings.warn(
                f"arrived EV '{veh_id}' without charge or arrival position; " "skipped",
                RuntimeWarning,
            )
            return None
        else:
            return Arrival(
                veh_id=veh_id,
                charge_wh=charge,
                time_s=self.current_time(),
                coords=coords,
            )

    def _get_trip_steps(self) -> list[TripStep]:
        current_time = self.current_time()
        running = []
        running_ev_ids = self._get_ev_ids(state="running")
        for veh_id in running_ev_ids:
            coords = self._coords(veh_id, "running")
            if coords is None:
                continue
            running.append(TripStep(veh_id=veh_id, coords=coords, time_s=current_time))
        return running

    def _get_ev_ids(
        self, state: Literal["arrived", "departed", "running"]
    ) -> list[str]:
        if state == "arrived":
            veh_ids = libsumo.simulation.getArrivedIDList()
        elif state == "departed":
            veh_ids = libsumo.simulation.getDepartedIDList()
        elif state == "running":
            departed_ids = libsumo.simulation.getDepartedIDList()
            veh_ids = [
                veh_id
                for veh_id in libsumo.vehicle.getIDList()
                if veh_id not in departed_ids
            ]
        else:
            raise ValueError(
                f"state must be 'arrived', 'departed' or 'running', not {state}"
            )
        return [veh_id for veh_id in veh_ids if veh_id in self.evs]

    def _driver_id(self, veh_id: str) -> str | None:
        try:
            return self.evs[veh_id].get("driver_id")
        except KeyError:
            pass
        try:
            driver_id = libsumo.vehicle.getParameter(veh_id, "driver_id")
        except libsumo.TraCIException:
            return None
        # TODO: temporary workaround as long as EV_SUT and EV_MUT don't
        # have driver_id
        if driver_id == "":
            return veh_id
        return driver_id

    def _coords(
        self, veh_id: str, state: Literal["arrived", "departed", "running"]
    ) -> tuple[float, float] | None:
        if state == "arrived":
            return self._arrival_coords(veh_id)
        elif state in ["departed", "running"]:
            try:
                x, y = libsumo.vehicle.getPosition(veh_id)
            except libsumo.TraCIException:
                return None
            # A vehicle off the road (e.g. while teleporting) has no position.
            if libsumo.constants.INVALID_DOUBLE_VALUE in (x, y):
                return None
            return libsumo.simulation.convertGeo(x, y)
        else:
            raise ValueError(
                f"state must be 'arrived', 'departed' or 'running', not {state}"
            )

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
            self._update_battery(veh_id, power_kw, result["charge_wh"])
            new_mode = self._determine_mode(
                power_kw, result["charge_wh"], veh_id, current_time
            )
            self._switch_emission_class(veh_id, new_mode)

    def _update_battery(self, veh_id: str, power_kw: float, charge_wh: float):
        phev = self.phevs[veh_id]
        sumo_kw = power_kw if phev["current_mode"] == "EV" else 0.0
        battery_kw = self._battery_power_kw(power_kw, charge_wh, phev["capacity_wh"])
        step_h = libsumo.simulation.getDeltaT() / 3600
        new_charge_wh = _clamp(
            charge_wh + (sumo_kw - battery_kw) * 1000 * step_h, 0.0, phev["capacity_wh"]
        )
        libsumo.vehicle.setParameter(
            veh_id, "device.battery.actualBatteryCapacity", str(new_charge_wh)
        )

    def _battery_power_kw(
        self, power_kw: float, charge_wh: float, capacity_wh: float
    ) -> float:
        """Computes the power that is drawn from (positive) or credited to (negative)
        the battery."""
        battery_kw = _clamp(power_kw, -self.ev_power_limit_kw, self.ev_power_limit_kw)
        if charge_wh / capacity_wh < self.reserve_soc:
            return min(battery_kw, 0.0)
        return battery_kw

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
    ) -> Literal["EV", "ICE"]:
        """PHEVs drive in ICE mode below the reserve SOC, when they demand more power
        than the electric motor can provide, or within ice_duration of the last ICE
        activation; the first two cases restart that timer."""
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

    def _switch_emission_class(self, veh_id: str, new_mode: str):
        phev = self.phevs[veh_id]
        if phev["current_mode"] == new_mode:
            return
        if new_mode == "ICE":
            libsumo.vehicle.setEmissionClass(veh_id, "HBEFA4/PC_petrol_Euro-6ab")
        else:
            libsumo.vehicle.setEmissionClass(veh_id, "Energy/unknown")
        phev["current_mode"] = new_mode

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
