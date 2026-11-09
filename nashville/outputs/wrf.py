from dataclasses import dataclass
from base import Cell, CellObservation


@dataclass
class Weather(Cell):
    pass


@dataclass
class WeatherObservation(CellObservation):
    entity = Weather

    temperature_c: float
    humidity_kg_kg: float
    wind_speed_m_s: float
    precipitation_mm: float
    pressure_pa: float
    shortwave_w_m2: float
