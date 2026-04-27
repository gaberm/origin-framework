from __future__ import annotations
from abc import abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Optional
from shapely import Geometry


@dataclass(frozen=True, kw_only=True)
class BaseRecord:
    table_name: ClassVar[str]
    primary_key: ClassVar[tuple[str, ...]]
    indexed: ClassVar[tuple[str, ...]] = ()
    domain: ClassVar[Optional[str]] = None
    diagnostic: ClassVar[bool] = False

    global_time: float
    geometry: Geometry
    height: Optional[float] = None
    cell_ids: Optional[list[str]] = None

    @classmethod
    @abstractmethod
    def from_dict(cls, d: dict) -> BaseRecord: ...

    def __post_init__(self):
        self._validate_primary_key()
        self._validate_indexed()

    def _validate_primary_key(self):
        for key in self.primary_key:
            if not hasattr(self, key):
                raise ValueError(
                    f"Primary key '{key}' not found in record of type '{self.__class__.__name__}'."
                )

    def _validate_indexed(self):
        for field in self.indexed:
            if not hasattr(self, field):
                raise ValueError(
                    f"Indexed field '{field}' not found in record of type '{self.__class__.__name__}'."
                )
