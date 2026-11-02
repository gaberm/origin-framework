import pytest
from dataclasses import dataclass
from base import Entity, KeyJoin, Observation, Record, ref, refs


@dataclass(kw_only=True)
class Driver(Entity):
    id_field = "driver_id"
    driver_id: str


@dataclass(kw_only=True)
class Car(Entity):
    id_field = "veh_id"
    veh_id: str
    driver_id: str = ref(Driver)
    # Named, because Garage is defined below.
    garage_id: str | None = ref("Garage")


@dataclass(kw_only=True)
class Garage(Entity):
    id_field = "garage_id"
    garage_id: str


@dataclass(kw_only=True)
class Port(Entity):
    id_field = ("port_id", "station_id")
    port_id: str
    station_id: str


def test_refs_resolve_classes_and_names():
    assert refs(Car) == {"driver_id": Driver, "garage_id": Garage}
    assert refs(Driver) == {}


def test_refs_in_observations():
    @dataclass(kw_only=True)
    class Position(Observation):
        id_field = "veh_id"
        entity = Car
        veh_id: str
        driver_id: str = ref(Driver)

    assert refs(Position) == {"driver_id": Driver}


def test_unknown_name():
    @dataclass(kw_only=True)
    class Bike(Entity):
        id_field = "veh_id"
        veh_id: str
        owner_id: str = ref("Owner")

    with pytest.raises(NameError, match=r"Bike.owner_id: ref\('Owner'\) names no class"):
        refs(Bike)


@pytest.mark.parametrize("target", [Entity, Record, "pytest"])
def test_target_must_be_an_entity(target):
    @dataclass(kw_only=True)
    class Bike(Entity):
        id_field = "veh_id"
        veh_id: str
        owner_id: str = ref(target)

    with pytest.raises(TypeError, match="Bike.owner_id: ref\\(\\) must name an Entity"):
        refs(Bike)


def test_target_with_composite_id():
    @dataclass(kw_only=True)
    class Session(Entity):
        id_field = "session_id"
        session_id: str
        port_id: str = ref(Port)

    with pytest.raises(TypeError, match="Port has the composite id"):
        refs(Session)


def test_target_whose_primary_key_isnt_its_id():
    @dataclass(kw_only=True)
    class Tour(Entity):
        id_field = "tour_id"
        primary_key = ("tour_id", "start_s")
        tour_id: str

    @dataclass(kw_only=True)
    class Leg(Entity):
        id_field = "leg_id"
        leg_id: str
        tour_id: str = ref(Tour)

    with pytest.raises(TypeError, match="Tour's primary key"):
        refs(Leg)


def test_ref_field_must_be_a_string():
    @dataclass(kw_only=True)
    class Bike(Entity):
        id_field = "veh_id"
        veh_id: str
        driver_id: int = ref(Driver)

    with pytest.raises(TypeError, match="Bike.driver_id: a ref\\(\\) field holds an id"):
        refs(Bike)


@pytest.mark.parametrize(
    "left, right, expected",
    [
        (Car, Driver, ((Car, "driver_id"), (Driver, "driver_id"))),
        (Driver, Car, ((Driver, "driver_id"), (Car, "driver_id"))),
        (Garage, Car, ((Garage, "garage_id"), (Car, "garage_id"))),
    ],
)
def test_key_join_uses_the_ref(left, right, expected):
    join = KeyJoin(left, right, join_type="left")
    assert (join.left_record, join.left_field) == expected[0]
    assert (join.right_record, join.right_field) == expected[1]
    assert join.join_type == "left"


def test_key_join_without_ref():
    with pytest.raises(ValueError, match="no ref\\(\\) between them"):
        KeyJoin(Driver, Garage)


def test_key_join_with_several_refs():
    @dataclass(kw_only=True)
    class Swap(Entity):
        id_field = "swap_id"
        swap_id: str
        from_driver_id: str = ref(Driver)
        to_driver_id: str = ref(Driver)

    with pytest.raises(ValueError, match="several ref\\(\\) fields"):
        KeyJoin(Swap, Driver)


def test_key_join_mixed_arguments():
    with pytest.raises(TypeError, match="both be"):
        KeyJoin(Car, (Driver, "driver_id"))
