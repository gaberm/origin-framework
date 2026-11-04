import pytest
from dataclasses import dataclass
from datetime import timedelta
from base import Entity


@dataclass(kw_only=True)
class Vehicle(Entity):
    id_field = "veh_id"

    veh_id: str


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (5, None, (5.0, None)),
        (5.5, 10, (5.5, 10.0)),
        (timedelta(minutes=1), timedelta(minutes=2), (60.0, 120.0)),
        (None, None, (None, None)),
        (3.0, 3.0, (3.0, 3.0)),
    ],
)
def test_valid_start_end(start, end, expected):
    vehicle = Vehicle(veh_id="v1", start_s=start, end_s=end)
    assert (vehicle.start_s, vehicle.end_s) == expected


@pytest.mark.parametrize("attr", ["start", "end"])
@pytest.mark.parametrize("value", ["5", True, [5]])
def test_invalid_start_end_type(attr, value):
    kwargs = {"start": 0.0, attr: value}
    with pytest.raises(TypeError, match=f"Vehicle.{attr} must be a number of seconds"):
        Vehicle(veh_id="v1", **kwargs)


@pytest.mark.parametrize("attr", ["start", "end"])
def test_negative_start_end(attr):
    kwargs = {"start": 0.0, attr: -1.0}
    with pytest.raises(ValueError, match=f"Vehicle.{attr} must be non-negative"):
        Vehicle(veh_id="v1", **kwargs)


def test_end_before_start():
    with pytest.raises(ValueError, match="Vehicle.end_s must not be before start_s"):
        Vehicle(veh_id="v1", start_s=10.0, end_s=5.0)


@dataclass(kw_only=True)
class Depot(Entity):
    id_field = "depot_id"
    initialize_only = True

    depot_id: str


def test_initialize_only_is_not_a_field():
    from dataclasses import fields

    assert "initialize_only" not in {field.name for field in fields(Depot)}
    assert Vehicle.initialize_only is False


def test_initialize_only_without_times():
    depot = Depot(depot_id="d1", start_s=None)
    assert (depot.start_s, depot.end_s) == (None, None)


@pytest.mark.parametrize("attr", ["start_s", "end_s"])
def test_initialize_only_rejects_times(attr):
    kwargs = {"start_s": None, attr: 5.0}
    with pytest.raises(ValueError, match=f"Depot is initialize_only.*{attr} must be None"):
        Depot(depot_id="d1", **kwargs)


def test_initialize_only_must_be_boolean():
    with pytest.raises(TypeError, match="Hub.initialize_only must be a boolean"):

        @dataclass(kw_only=True)
        class Hub(Entity):
            id_field = "hub_id"
            initialize_only = "yes"

            hub_id: str
