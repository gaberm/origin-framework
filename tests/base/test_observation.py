import pytest
from dataclasses import dataclass, fields
from base import Entity, StartObservation, Observation


@dataclass(kw_only=True)
class Vehicle(Entity):
    id_field = "veh_id"

    veh_id: str


def test_valid_observation():
    @dataclass(kw_only=True)
    class Departure(Observation):
        id_field = "veh_id"
        observation_type = "start"
        entity = Vehicle

        veh_id: str

    assert Departure.primary_key == ("veh_id", "time_s")


def test_default_observation_type():
    @dataclass(kw_only=True)
    class Position(Observation):
        id_field = "veh_id"
        entity = Vehicle

        veh_id: str

    assert Position.observation_type == "update"


def test_missing_entity():
    with pytest.raises(ValueError, match="BadObservation must declare an entity"):

        @dataclass(kw_only=True)
        class BadObservation(Observation):
            id_field = "veh_id"

            veh_id: str


@pytest.mark.parametrize("bad_entity", [Entity, str, Vehicle(veh_id="v1", start_s=0.0)])
def test_invalid_entity(bad_entity):
    with pytest.raises(
        TypeError, match="BadObservation.entity must be an Entity subclass"
    ):

        @dataclass(kw_only=True)
        class BadObservation(Observation):
            id_field = "veh_id"
            entity = bad_entity

            veh_id: str


def test_id_field_from_entity():
    @dataclass(kw_only=True)
    class Position(Observation):
        entity = Vehicle

        x: float

    assert Position.id_field == ("veh_id",)
    assert Position.primary_key == ("veh_id", "time_s")
    assert "veh_id" in {field.name for field in fields(Position)}


def test_id_field_from_composite_entity():
    @dataclass(kw_only=True)
    class Port(Entity):
        id_field = ("port_id", "station_id")

    @dataclass(kw_only=True)
    class Opened(Observation):
        entity = Port

    assert Opened.primary_key == ("port_id", "station_id", "time_s")


def test_id_field_mismatch():
    with pytest.raises(
        ValueError, match=r"BadObservation.id_field must match Vehicle.id_field"
    ):

        @dataclass(kw_only=True)
        class BadObservation(Observation):
            id_field = "driver_id"
            entity = Vehicle

            driver_id: str


def test_invalid_observation_type():
    with pytest.raises(
        ValueError, match="BadObservation.observation_type must be one of"
    ):

        @dataclass(kw_only=True)
        class BadObservation(Observation):
            id_field = "veh_id"
            observation_type = "creates"
            entity = Vehicle

            veh_id: str


def test_start_observation_is_completed_by_its_entity():
    class Departure(StartObservation):
        charge_wh: float

    @dataclass(kw_only=True)
    class Trip(Entity):
        id_field = "veh_id"
        start_observation = Departure

        veh_id: str

    assert Trip.start_observation is Departure
    assert Departure.entity is Trip
    assert Departure.observation_type == "start"
    assert Departure.id_field == ("veh_id",)
    assert Departure.primary_key == ("veh_id", "time_s")
    assert Departure.table_name == "Departure"
    assert [f.name for f in fields(Departure)] == ["charge_wh", "veh_id", "time_s"]
    assert [f.name for f in fields(Trip)] == ["start", "end", "veh_id"]
    departure = Departure(veh_id="v1", charge_wh=4e4, time_s=300)
    assert departure.time_s == 300.0


def test_start_observation_with_composite_id():
    class Opened(StartObservation):
        load_kw: float

    @dataclass(kw_only=True)
    class Port(Entity):
        id_field = ("port_id", "station_id")
        start_observation = Opened

        port_id: str
        station_id: str

    assert Opened.primary_key == ("port_id", "station_id", "time_s")


def test_start_observation_validates_like_an_observation():
    class Departure(StartObservation):
        charge_wh: float

    @dataclass(kw_only=True)
    class Trip(Entity):
        id_field = "veh_id"
        start_observation = Departure

        veh_id: str

    with pytest.raises(TypeError, match="Departure.veh_id must be a string"):
        Departure(veh_id=1, charge_wh=4e4, time_s=300)


def test_start_observation_belongs_to_one_entity():
    class Departure(StartObservation):
        charge_wh: float

    @dataclass(kw_only=True)
    class Trip(Entity):
        id_field = "veh_id"
        start_observation = Departure

        veh_id: str

    with pytest.raises(TypeError, match="already the start observation of Trip"):

        @dataclass(kw_only=True)
        class OtherTrip(Entity):
            id_field = "veh_id"
            start_observation = Departure

            veh_id: str


def test_start_observation_subclass_entity_keeps_it():
    class Departure(StartObservation):
        charge_wh: float

    @dataclass(kw_only=True)
    class Trip(Entity):
        id_field = "veh_id"
        start_observation = Departure

        veh_id: str

    @dataclass(kw_only=True)
    class EVTrip(Trip):
        battery_id: str

    assert EVTrip.start_observation is Departure
    assert Departure.entity is Trip


def test_start_observation_without_dataclass():
    @dataclass(kw_only=True)
    class Departure(StartObservation):
        charge_wh: float

    with pytest.raises(TypeError, match="must be declared without @dataclass"):

        @dataclass(kw_only=True)
        class Trip(Entity):
            id_field = "veh_id"
            start_observation = Departure

            veh_id: str


def test_entity_rejects_other_start_observation():
    with pytest.raises(
        TypeError, match="Trip.start_observation must be a StartObservation"
    ):

        @dataclass(kw_only=True)
        class Trip(Entity):
            id_field = "veh_id"
            start_observation = Vehicle

            veh_id: str
