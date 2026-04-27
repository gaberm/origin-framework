from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from shapely import Geometry


@dataclass
class ExternalDataset:
    table_name: str
    primary_key: tuple[str, ...]
    data: dict[str, list[any]]
    timestep_column: Optional[str] = None
    geometry_column: Optional[str] = None
    h3_index: bool = False
    indexed: tuple[str, ...] = ()
    domain: Optional[str] = None

    def __post_init__(self):
        self._validate_primary_key()
        self._validate_data()
        self._validate_timestep_column()
        self._validate_geometry_column()
        self._validate_indexed_columns()

    def _validate_primary_key(self):
        for key in self.primary_key:
            if key not in self.data:
                raise ValueError(
                    f"Primary key '{key}' not found in dataset '{self.table_name}'."
                )

    def _validate_data(self):
        lengths = set(len(v) for v in self.data.values())
        if len(lengths) > 1:
            raise ValueError(
                f"All columns in dataset '{self.table_name}' must have the same number of rows. Found lengths: {lengths}"
            )

    def _validate_timestep_column(self):
        if self.timestep_column and self.timestep_column not in self.data:
            raise ValueError(
                f"Timestep column '{self.timestep_column}' not found in dataset '{self.table_name}'."
            )

    def _validate_geometry_column(self):
        if self.geometry_column and self.geometry_column not in self.data:
            raise ValueError(
                f"Geometry column '{self.geometry_column}' not found in dataset '{self.table_name}'."
            )
        for row in self.data.get(self.geometry_column, []):
            if not isinstance(row, Geometry):
                raise ValueError(
                    f"Invalid geometry type in geometry column '{self.geometry_column}' of dataset '{self.table_name}': {type(row)}. Expected Shapely Geometry object."
                )

    def _validate_indexed_columns(self):
        for col in self.indexed:
            if col not in self.data:
                raise ValueError(
                    f"Indexed column '{col}' not found in dataset '{self.table_name}'."
                )


class DataAdapter(ABC):
    _registry: dict[str, type["DataAdapter"]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        DataAdapter._registry[cls.__name__] = cls

    @abstractmethod
    def load_data(self) -> list[ExternalDataset]: ...
