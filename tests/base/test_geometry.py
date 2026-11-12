import numpy as np
import pytest
from base import Geometry


def test_invalid_shape():
    with pytest.raises(ValueError, match=".shape must be one of"):
        _ = Geometry(shape="BADSHAPE", coords=(0.0, 0.0))


def test_point_too_short():
    with pytest.raises(
        ValueError, match="coords must be a \\(float, float\\) pair for a POINT"
    ):
        _ = Geometry(shape="POINT", coords=(0.0,))


def test_point_wrong_type():
    with pytest.raises(
        ValueError, match="coords must be a \\(float, float\\) pair for a POINT"
    ):
        _ = Geometry(shape="POINT", coords=("a", "b"))


def test_linestring_too_short():
    with pytest.raises(
        ValueError, match="\\(float, float\\) pairs for a LINESTRING"
    ):
        _ = Geometry(shape="LINESTRING", coords=[(0.0, 0.0)])


def test_polygon_too_short():
    with pytest.raises(
        ValueError, match="\\(float, float\\) pairs for a POLYGON"
    ):
        _ = Geometry(shape="POLYGON", coords=[(0.0, 0.0), (1.0, 1.0)])


def test_pair_wrong_type():
    with pytest.raises(
        ValueError, match="\\(float, float\\) pairs for a LINESTRING"
    ):
        _ = Geometry(shape="LINESTRING", coords=[("a", "b"), (1.0, 1.0)])


def test_valid_point():
    assert Geometry(shape="POINT", coords=(1.0, 2.0)).coords == (1.0, 2.0)


def test_valid_linestring():
    assert Geometry(shape="LINESTRING", coords=[(0.0, 0.0), (1.0, 1.0)]).coords == [
        (0.0, 0.0),
        (1.0, 1.0),
    ]


def test_valid_polygon():
    assert Geometry(
        shape="POLYGON", coords=[(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]
    ).coords == [(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]


def test_numpy_point():
    coords = (np.float32(-86.7), np.float32(36.1))
    assert Geometry(shape="POINT", coords=coords).coords == coords


def test_numpy_linestring():
    coords = [(np.int64(0), np.int64(0)), (np.float32(1.0), np.float64(1.0))]
    assert Geometry(shape="LINESTRING", coords=coords).coords == coords
