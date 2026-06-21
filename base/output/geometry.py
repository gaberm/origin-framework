from dataclasses import dataclass
from typing import Literal

SHAPE_TYPE = Literal["POINT", "LINESTRING", "POLYGON"]


@dataclass(kw_only=True)
class Geometry:
    coords: list
    shape: SHAPE_TYPE
    epsg_code: int = 4326
    h3_ids: str | list[str] | None = None
    coords_4326: list | None = None

    def _validate_shape(self):
        if self.shape == "POINT":
            assert len(self.coords) == 2, "POINT shape must have a coordinate pair."
        else:
            assert all(isinstance(c, (list, tuple)) for c in self.coords), \
                f"{self.shape} shape must have a list of coordinate pairs."
