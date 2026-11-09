from datetime import datetime, timezone
import numpy as np
import xarray as xr
from adapter import DataAdapter
from base import grid_cells, grid_observations
from ..outputs.wrf import Weather, WeatherObservation


class WRFAdapter(DataAdapter):
    output_types = [Weather, WeatherObservation]

    def __init__(self, name: str, file_path: str, **kwargs):
        super().__init__(name, **kwargs)
        self.file_path = file_path

    def load_data(self) -> list[Weather | WeatherObservation]:
        with xr.open_dataset(self.file_path, engine="netcdf4") as dataset:
            return self._load_cells(dataset) + self._load_observations(dataset)

    def _load_cells(self, dataset: xr.Dataset) -> list[Weather]:
        return grid_cells(
            Weather,
            latitudes=dataset["XLAT"].isel(Time=0).values,
            longitudes=dataset["XLONG"].isel(Time=0).values,
        )

    def _load_observations(self, dataset: xr.Dataset) -> list[WeatherObservation]:
        # WRF times are UTC, written as b"2026-01-18_18:00:00".
        timestamps = [
            datetime.strptime(raw_time.decode(), "%Y-%m-%d_%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            for raw_time in dataset["Times"].values
        ]
        observations = []
        for time_index, simulation_time in self.times_in_run(timestamps):
            step = dataset.isel(Time=time_index)
            observations += grid_observations(
                WeatherObservation,
                simulation_time,
                {
                    "temperature_c": step["T2"].values - 273.15,
                    "humidity_kg_kg": step["Q2"].values,
                    "wind_speed_m_s": np.hypot(step["U10"].values, step["V10"].values),
                    "precipitation_mm": step["RAINC"].values + step["RAINNC"].values,
                    "pressure_pa": step["PSFC"].values,
                    "shortwave_w_m2": step["SWDOWN"].values,
                },
            )
        return observations
