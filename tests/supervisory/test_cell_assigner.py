import pytest
import h3
from supervisory.space.cell_assigner import _cells_for_shape

RESOLUTION = 5

coord_uni_4326 = (35.95, -83.93)
coord_lab_4326 = (35.93, -84.31)
coord_airport_4326 = (35.81, -84.00)

linestring_coords = [coord_uni_4326, coord_lab_4326]
linestring_coords_duplicates = [coord_uni_4326, coord_uni_4326, coord_lab_4326]
polygon_coords = [coord_uni_4326, coord_lab_4326, coord_airport_4326]


def test_bad_shape():
    with pytest.raises(ValueError, match="Unsupported shape type"):
        _cells_for_shape("CIRCLE", (0.0, 0.0), RESOLUTION)


class TestPoint:
    def test_returns_single_element_list(self):
        result = _cells_for_shape("POINT", coord_uni_4326, RESOLUTION)
        assert len(result) == 1

    def test_cell_is_valid_h3(self):
        result = _cells_for_shape("POINT", coord_uni_4326, RESOLUTION)
        assert h3.is_valid_cell(result[0])

    def test_resolution_affects_cell_id(self):
        cell_res5 = _cells_for_shape("POINT", coord_uni_4326, 5)[0]
        cell_res7 = _cells_for_shape("POINT", coord_uni_4326, 7)[0]
        assert cell_res5 != cell_res7

    def test_matches_direct_h3_lookup(self):
        result = _cells_for_shape("POINT", coord_uni_4326, RESOLUTION)
        expected = h3.latlng_to_cell(coord_uni_4326[1], coord_uni_4326[0], RESOLUTION)
        assert result == [expected]


class TestLinestring:
    def test_returns_non_empty_list(self):
        results = _cells_for_shape("LINESTRING", linestring_coords, RESOLUTION)
        assert results

    def test_cells_are_valid_h3(self):
        results = _cells_for_shape("LINESTRING", linestring_coords, RESOLUTION)
        assert all(h3.is_valid_cell(cell) for cell in results)

    def test_resolution_affects_cell_id(self):
        cell_res5 = _cells_for_shape("LINESTRING", linestring_coords, 5)[0]
        cell_res7 = _cells_for_shape("LINESTRING", linestring_coords, 7)[0]
        assert cell_res5 != cell_res7

    def test_no_duplicate_h3_ids(self):
        results = _cells_for_shape(
            "LINESTRING", linestring_coords_duplicates, RESOLUTION
        )
        assert len(results) == len(set(results))


class TestPolygon:
    def test_returns_non_empty_list(self):
        results = _cells_for_shape("POLYGON", polygon_coords, RESOLUTION)
        assert results

    def test_cells_are_valid_h3(self):
        results = _cells_for_shape("POLYGON", polygon_coords, RESOLUTION)
        assert all(h3.is_valid_cell(cell) for cell in results)

    def test_resolution_affects_cell_id(self):
        cell_res5 = _cells_for_shape("POLYGON", polygon_coords, 5)[0]
        cell_res7 = _cells_for_shape("POLYGON", polygon_coords, 7)[0]
        assert cell_res5 != cell_res7

    def test_matches_direct_h3_lookup(self):
        result = _cells_for_shape("POLYGON", polygon_coords, RESOLUTION)
        expected = list(
            h3.h3shape_to_cells(
                h3.LatLngPoly([(lat, lon) for lon, lat in polygon_coords]), RESOLUTION
            )
        )
        assert result == expected
