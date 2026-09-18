import numpy as np
import pytest
from datetime import timedelta
from dataclasses import dataclass
from base import Timestamp


@dataclass(kw_only=True)
class SimpleTimestamp(Timestamp):
    pass


def test_bad_time_field():
    with pytest.raises(TypeError, match="time_field must be a field in"):

        @dataclass(kw_only=True)
        class BadTimestamp(Timestamp):
            time_field = "missing_field"


def test_bad_time_type():
    with pytest.raises(
        TypeError, match=r"\.time_s must be a number of seconds or a timedelta"
    ):
        SimpleTimestamp(time_s="bad_time")


@pytest.mark.parametrize("time", [True, None, timedelta])
def test_non_numeric_time_raises(time):
    with pytest.raises(TypeError, match="must be a number of seconds or a timedelta"):
        SimpleTimestamp(time_s=time)


def test_negative_time_raises():
    with pytest.raises(ValueError, match="must be non-negative"):
        SimpleTimestamp(time_s=-1.0)


def test_valid_record():
    record = SimpleTimestamp(time_s=1.0)
    assert record.time_s == 1.0


@pytest.mark.parametrize("time", [np.int64(1), np.float32(1.5)])
def test_numpy_time(time):
    assert SimpleTimestamp(time_s=time).time_s == time


def test_negative_numpy_time_raises():
    with pytest.raises(ValueError, match="must be non-negative"):
        SimpleTimestamp(time_s=np.float64(-1.0))


def test_timedelta_time_stored_as_seconds():
    record = SimpleTimestamp(time_s=timedelta(minutes=1, milliseconds=500))
    assert record.time_s == 60.5
    assert type(record.time_s) is float


def test_negative_timedelta_raises():
    with pytest.raises(ValueError, match="must be non-negative"):
        SimpleTimestamp(time_s=timedelta(seconds=-1))
