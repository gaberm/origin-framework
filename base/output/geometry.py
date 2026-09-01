from dataclasses import dataclass
from typing import Literal, get_args

SHAPE_TYPE = Literal["POINT", "LINESTRING", "POLYGON"]


@dataclass(kw_only=True)
class Geometry:
    shape: SHAPE_TYPE
    coords: tuple[float, float] | list[tuple[float, float]]
    srid: int = 4326
    h3_ids: str | list[str] | None = None
    coords_4326: list | None = None

    def __post_init__(self):
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
                isinstance(coord, (float, int)) for coord in self.coords
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
            and all(isinstance(val, (float, int)) for val in coord)
        )


def to_wkt(
    coords: tuple[float, float] | list[tuple[float, float]], shape: SHAPE_TYPE
) -> str:
    if shape == "POINT":
        x, y = coords
        return f"POINT({x} {y})"
    points = list(coords)
    if shape == "POLYGON" and points[0] != points[-1]:
        points = points + [points[0]]
    coord_list = ", ".join(f"{x} {y}" for x, y in points)
    return f"{shape}({coord_list})" if shape == "LINESTRING" else f"{shape}(({coord_list}))"


def to_ewkt(
    coords: tuple[float, float] | list[tuple[float, float]],
    shape: SHAPE_TYPE,
    srid: int,
) -> str:
    return f"SRID={srid};{to_wkt(coords, shape)}"
