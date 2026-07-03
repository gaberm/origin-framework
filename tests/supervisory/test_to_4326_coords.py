import pytest
from pyproj import Transformer
from supervisory.geo.coords_converter import to_4326_coords

coord_uni_4326 = (35.95, -83.93)
coord_lab_4326 = (35.93, -84.31)

coord_uni_3857 = Transformer.from_crs(4326, 3857, always_xy=True).transform(
    *coord_uni_4326
)
coord_lab_3857 = Transformer.from_crs(4326, 3857, always_xy=True).transform(
    *coord_lab_4326
)


def test_to_4326_coords_espg_code_4326():
    assert to_4326_coords(coord_uni_4326, "POINT", 4326) == coord_uni_4326


def test_to_4326_coords_bad_espg_code():
    with pytest.raises(ValueError, match="Invalid EPSG code"):
        to_4326_coords(coord_uni_4326, "POINT", 0)


def test_to_4326_coords_point_shape():
    assert to_4326_coords(coord_uni_3857, "POINT", 3857) == pytest.approx(
        coord_uni_4326
    )


def test_to_4326_coords_other_shapes():
    result = to_4326_coords([coord_uni_3857, coord_lab_3857], "LINESTRING", 3857)
    assert result[0] == pytest.approx(coord_uni_4326)
    assert result[1] == pytest.approx(coord_lab_4326)
