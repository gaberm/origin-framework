from dataclasses import dataclass
from datetime import timedelta
from typing import TypeVar
import numpy as np
from base.output.entity import Entity
from base.output.geometry import SHAPE_TYPE, Geometry
from base.output.observation import Observation


@dataclass(kw_only=True)
class Cell(Entity, Geometry):
    id_field = "cell_id"

    cell_id: str
    row_idx: int
    column_idx: int
    shape: SHAPE_TYPE = "POLYGON"

    @staticmethod
    def make_id(row: int, column: int) -> str:
        """The cell_id of the cell at `row` and `column` of its grid."""
        return f"{row}_{column}"


@dataclass(kw_only=True)
class CellObservation(Observation):
    entity = Cell


CellType = TypeVar("CellType", bound=Cell)
CellObservationType = TypeVar("CellObservationType", bound=CellObservation)


def grid_cells(
    cell_type: type[CellType], latitudes: np.ndarray, longitudes: np.ndarray
) -> list[CellType]:
    """One cell per point of a grid, from 2D arrays of the latitudes and
    longitudes of the cells' centers. Each cell is the polygon between its
    corners, see _cell_corners. The cells exist for the whole run."""
    latitudes = np.asarray(latitudes, dtype=float)
    longitudes = np.asarray(longitudes, dtype=float)
    if latitudes.ndim != 2 or latitudes.shape != longitudes.shape:
        raise ValueError(
            f"latitudes and longitudes must be 2D arrays of the same shape; "
            f"got {latitudes.shape} and {longitudes.shape}"
        )
    corner_latitudes = _cell_corners(latitudes).tolist()
    corner_longitudes = _cell_corners(longitudes).tolist()
    rows, columns = latitudes.shape
    cells = []
    for row in range(rows):
        for column in range(columns):
            corners = [
                (row, column),
                (row, column + 1),
                (row + 1, column + 1),
                (row + 1, column),
            ]
            cells.append(
                cell_type(
                    cell_id=Cell.make_id(row, column),
                    row_idx=row,
                    column_idx=column,
                    start_s=None,
                    coords=[
                        (
                            corner_longitudes[corner_row][corner_column],
                            corner_latitudes[corner_row][corner_column],
                        )
                        for corner_row, corner_column in corners
                    ],
                )
            )
    return cells


def grid_observations(
    observation_type: type[CellObservationType],
    time: float | timedelta,
    fields: dict[str, np.ndarray],
) -> list[CellObservationType]:
    """One observation per cell of a grid at `time`, from 2D arrays of the
    values of each field, keyed by field name, e.g.
    {"temperature_c": temperatures}."""
    if not fields:
        raise ValueError("fields must name at least one field")
    arrays = {name: np.asarray(values) for name, values in fields.items()}
    shapes = {name: values.shape for name, values in arrays.items()}
    shape = next(iter(shapes.values()))
    if len(shape) != 2 or any(other != shape for other in shapes.values()):
        raise ValueError(f"fields must be 2D arrays of the same shape; got {shapes}")
    # Python numbers, not numpy scalars, for the records.
    values = {name: array.tolist() for name, array in arrays.items()}
    rows, columns = shape
    return [
        observation_type(
            cell_id=Cell.make_id(row, column),
            **{observation_type.time_field: time},
            **{
                name: field_values[row][column] for name, field_values in values.items()
            },
        )
        for row in range(rows)
        for column in range(columns)
    ]


def _cell_corners(centers: np.ndarray) -> np.ndarray:
    """Corner points of each grid cell, shape (rows + 1, columns + 1), from its
    center points. Interior corners average the four surrounding centers; the
    outer edge is extended outward by half a cell."""
    padded = np.pad(centers, 1, mode="reflect", reflect_type="odd")
    return (padded[:-1, :-1] + padded[1:, :-1] + padded[:-1, 1:] + padded[1:, 1:]) / 4
