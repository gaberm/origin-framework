from dataclasses import dataclass
from numbers import Real
from typing import Literal, get_args

SHAPE_TYPE = Literal["POINT", "LINESTRING", "POLYGON"]


@dataclass(kw_only=True)
class Geometry:
    """Base class for records that have a location.

    `shape` is "POINT", "LINESTRING" or "POLYGON".

    `coords` is one (x, y) pair for a POINT, otherwise a list of pairs,
    e.g. [(x1, y1), (x2, y2), ...]. For 4326, x is longitude and y latitude.

    `epsg_code` is the EPSG code of the coordinate system `coords` are in. The default
    is 4326 (WGS84).

    The state memory stores `coords` in 4326 and adds the H3 cells they
    cover as `h3_ids`. It doesn't store `epsg_code`, which is always 4326
    once stored.
    """

    shape: SHAPE_TYPE
    coords: tuple[float, float] | list[tuple[float, float]]
    epsg_code: int = 4326

    def __post_init__(self):
        parent_post_init = getattr(super(), "__post_init__", None)
        if parent_post_init is not None:
            parent_post_init()
        self._validate_shape()
        self._validate_coords()

    def _validate_shape(self):
        if self.shape not in get_args(SHAPE_TYPE):
            raise ValueError(
                f"{type(self).__name__}.shape must be one of {get_args(SHAPE_TYPE)}; got {self.shape!r}"
            )

    def _validate_coords(self):
        if self.shape == "POINT":
            if len(self.coords) != 2 or not all(
                isinstance(coord, Real) for coord in self.coords
            ):
                raise ValueError("POINT coords must be a (float, float) pair")
        else:
            min_points = 2 if self.shape == "LINESTRING" else 3
            if len(self.coords) < min_points or not all(
                self._is_coord_pair(coord) for coord in self.coords
            ):
                raise ValueError(
                    f"{self.shape} coords must be a list of (float, float) pairs."
                )

    @staticmethod
    def _is_coord_pair(coord) -> bool:
        return (
            isinstance(coord, (list, tuple))
            and len(coord) == 2
            and all(isinstance(val, Real) for val in coord)
        )
