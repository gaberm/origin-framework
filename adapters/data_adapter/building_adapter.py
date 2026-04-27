from __future__ import annotations
import uuid
import pandas as pd
from shapely.geometry import Polygon
from adapters.data_adapter import DataAdapter, ExternalDataset


class BuildingAdapter(DataAdapter):
    def __init__(self, buildings_path: str, energy_path: str):
        self.buildings_path = buildings_path
        self.energy_path = energy_path

    def load(self) -> list[ExternalDataset]:
        return ExternalDataset(
            table_name="buildings",
            primary_key="uuid",
            data=self._load_buildings(),
            geometry_column="geometry",
        )

    def _load_buildings(self) -> dict[str, list[any]]:
        data = pd.read_csv(self.buildings_path).to_dict(orient="list")
        data["uuid"] = [str(uuid.uuid4()) for _ in range(len(data["ID"]))]
        data["geometry"] = data["Footprint2D"].apply(self._parse_footprint)
        return data

    def _parse_footprint(footprint_str: str) -> Polygon:
        coords = [
            (float(lon), float(lat))
            for part in footprint_str.split("_")
            for lat, lon in [part.split("/")]
        ]
        return Polygon(coords)
