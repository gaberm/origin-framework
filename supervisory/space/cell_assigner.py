import dataclasses
import h3
from typing import Any
from base import Geometry, SHAPE_TYPE
from base.output.dataset import Dataset


def assign_cells(output: Any, resolution: int) -> Any:
    if isinstance(output, list):
        return assign_model_output(output, resolution)
    elif isinstance(output, Dataset):
        return assign_external_dataset(output, resolution)
    else:
        raise TypeError(
            "Cannot assign cells to output of type: {}".format(type(output))
        )


def assign_external_dataset(dataset: Dataset, resolution: int) -> Dataset:
    pass


def assign_model_output(outputs: list, resolution: int) -> list:
    results = []
    for output in outputs:
        if isinstance(output, Geometry):
            output = dataclasses.replace(
                output,
                h3_ids=_cells_for_shape(output.shape, output.coords_4326, resolution),
            )
        results.append(output)
    return results


def _cells_for_shape(
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
