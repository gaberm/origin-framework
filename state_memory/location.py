from functools import lru_cache
import h3
from pyproj.exceptions import CRSError
from pyproj import Transformer
from base import SHAPE_TYPE

H3_RESOLUTION = 9


def with_location(row: dict) -> dict:
    """A Geometry row with `coords` in 4326 and the H3 cells they cover, and
    without `epsg_code`, which has no column."""
    coords = to_4326_coords(row["coords"], row["shape"], row["epsg_code"])
    row = {key: value for key, value in row.items() if key != "epsg_code"}
    return {
        **row,
        "coords": coords,
        "h3_ids": h3_cells(row["shape"], coords, H3_RESOLUTION),
    }


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


def h3_cells(
    shape: SHAPE_TYPE,
    coords: tuple[float, float] | list[tuple[float, float]],
    resolution: int,
) -> list[str]:
    if shape == "POINT":
        return [h3.latlng_to_cell(coords[1], coords[0], resolution)]

    if shape == "LINESTRING":
        ids = []
        for i in range(len(coords) - 1):
            start = h3.latlng_to_cell(coords[i][1], coords[i][0], resolution)
            end = h3.latlng_to_cell(coords[i + 1][1], coords[i + 1][0], resolution)
            ids.extend(h3.grid_path_cells(start, end))
        return list(dict.fromkeys(ids))

    if shape == "POLYGON":
        return list(
            set(
                h3.h3shape_to_cells(
                    h3.LatLngPoly([(lat, lon) for lon, lat in coords]), resolution
                )
            )
        )

    raise ValueError(f"Unsupported shape type: {shape}")
