import numpy as np
import pytest
from dataclasses import dataclass
from datetime import timedelta
from base import Cell, CellObservation, grid_cells, grid_observations


@dataclass(kw_only=True)
class GridCell(Cell):
    pass


@dataclass(kw_only=True)
class GridObservation(CellObservation):
    entity = GridCell

    temperature_c: float
    pressure_pa: float


# Centers one degree apart, two rows by three columns.
LATITUDES = np.array([[10.0, 10.0, 10.0], [11.0, 11.0, 11.0]])
LONGITUDES = np.array([[20.0, 21.0, 22.0], [20.0, 21.0, 22.0]])


class TestGridCells:
    def test_one_cell_per_grid_point(self):
        cells = grid_cells(GridCell, LATITUDES, LONGITUDES)
        assert [(cell.row_idx, cell.column_idx) for cell in cells] == [
            (0, 0),
            (0, 1),
            (0, 2),
            (1, 0),
            (1, 1),
            (1, 2),
        ]
        assert cells[4].cell_id == Cell.make_id(1, 1) == "1_1"
        assert all(isinstance(cell, GridCell) for cell in cells)

    def test_cell_spans_halfway_to_its_neighbors(self):
        cell = grid_cells(GridCell, LATITUDES, LONGITUDES)[4]
        assert cell.coords == [(20.5, 10.5), (21.5, 10.5), (21.5, 11.5), (20.5, 11.5)]

    def test_cells_exist_for_the_whole_run(self):
        cell = grid_cells(GridCell, LATITUDES, LONGITUDES)[0]
        assert cell.start_s is None and cell.end_s is None

    def test_coords_are_python_floats(self):
        cell = grid_cells(GridCell, LATITUDES, LONGITUDES)[0]
        assert all(type(value) is float for corner in cell.coords for value in corner)

    def test_rejects_mismatched_shapes(self):
        with pytest.raises(ValueError, match="same shape"):
            grid_cells(GridCell, LATITUDES, LONGITUDES[:, :2])

    def test_rejects_1d_arrays(self):
        with pytest.raises(ValueError, match="2D arrays"):
            grid_cells(GridCell, LATITUDES[0], LONGITUDES[0])


class TestGridObservations:
    def test_one_observation_per_cell_matching_cell_ids(self):
        observations = grid_observations(
            GridObservation,
            timedelta(hours=1),
            {
                "temperature_c": np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
                "pressure_pa": np.full((2, 3), 101325.0),
            },
        )
        cells = grid_cells(GridCell, LATITUDES, LONGITUDES)
        assert [obs.cell_id for obs in observations] == [cell.cell_id for cell in cells]
        assert observations[5].temperature_c == 6.0
        assert observations[5].pressure_pa == 101325.0
        assert observations[5].time_s == 3600.0

    def test_values_are_python_numbers(self):
        observations = grid_observations(
            GridObservation,
            0.0,
            {
                "temperature_c": np.ones((1, 1), dtype=np.float32),
                "pressure_pa": np.ones((1, 1)),
            },
        )
        assert type(observations[0].temperature_c) is float

    def test_rejects_mismatched_shapes(self):
        with pytest.raises(ValueError, match="same shape"):
            grid_observations(
                GridObservation,
                0.0,
                {"temperature_c": np.ones((2, 3)), "pressure_pa": np.ones((3, 2))},
            )

    def test_rejects_no_fields(self):
        with pytest.raises(ValueError, match="at least one field"):
            grid_observations(GridObservation, 0.0, {})
