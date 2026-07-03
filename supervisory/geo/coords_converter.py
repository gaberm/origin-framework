from functools import lru_cache
from pyproj.exceptions import CRSError
from pyproj import Transformer
from base import Record, Geometry, SHAPE_TYPE
import dataclasses


@lru_cache(maxsize=None)
def _transformer_to_4326(epsg_code: int) -> Transformer:
    try:
        return Transformer.from_crs(epsg_code, 4326, always_xy=True)
    except CRSError as error:
        raise ValueError(f"Invalid EPSG code: {epsg_code}") from error


def to_4326_coords(
    coords: tuple[float, float] | list[tuple[float, float]],
    shape: SHAPE_TYPE,
    epsg_code: int,
) -> tuple[float, float] | list[tuple[float, float]]:
    if epsg_code == 4326:
        return coords

    project = _transformer_to_4326(epsg_code).transform
    if shape == "POINT":
        return project(*coords, errcheck=True)
    return [project(x, y, errcheck=True) for x, y in coords]


def convert_coords(output: Record) -> Record:
    if isinstance(output, Geometry):
        return dataclasses.replace(
            output,
            coords_4326=to_4326_coords(output.coords, output.shape, output.epsg_code),
        )
    else:
        return output
