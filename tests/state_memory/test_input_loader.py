import pytest
from base import Input, Fields, Filter, Equal, In, NotIn, KeyJoin, SpatialJoin, Latest
from state_memory.input_loader import InputLoader
from base import TimeWindow
from _records import Trip, Vehicle, Battery, VehicleLocation, Zone, RUN_ID


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def getconn(self):
        return self._conn

    def putconn(self, conn):
        pass


@pytest.fixture
def loader(postgresql):
    cur = postgresql.cursor()
    cur.execute(f"CREATE SCHEMA {RUN_ID}")
    cur.execute(f"CREATE TABLE {RUN_ID}.vehicle (veh_id TEXT, soc FLOAT, time_s FLOAT)")
    cur.execute(f"CREATE TABLE {RUN_ID}.battery (veh_id TEXT, capacity FLOAT)")
    cur.executemany(
        f"INSERT INTO {RUN_ID}.vehicle (veh_id, soc, time_s) VALUES (%s, %s, %s)",
        [
            ("veh_1", 0.5, 1.0),
            ("veh_2", 0.7, 2.0),
            ("veh_3", 0.9, 3.0),
            ("veh_1", 0.8, 2.5),  # second entry for veh_1, used in Latest tests
        ],
    )
    cur.executemany(
        f"INSERT INTO {RUN_ID}.battery (veh_id, capacity) VALUES (%s, %s)",
        [("veh_1", 100.0), ("veh_2", 200.0), ("veh_3", 300.0)],
    )
    postgresql.commit()
    return InputLoader(FakePool(postgresql), RUN_ID)


def test_window_read_policy(loader):
    spec = Input(name="vehicle", from_=Vehicle, select=Fields("veh_id", "soc"))
    result = loader.load_inputs(spec, TimeWindow(0.0, 2.5))
    assert len(result["vehicle"]) == 2  # t=1.0 and t=2.0; t=2.5 excluded


def test_latest_read_policy(loader):
    spec = Input(
        name="vehicle",
        from_=Vehicle,
        select=Fields("veh_id", "soc"),
        read_policy=Latest(by="veh_id"),
    )
    result = loader.load_inputs(spec, TimeWindow(0.0, 3.0))
    assert len(result["vehicle"]) == 2  # veh_3 at t=3.0 excluded by < end_time
    rows = {row["veh_id"]: row["soc"] for row in result["vehicle"]}
    assert rows["veh_1"] == 0.8  # t=2.5 beats t=1.0


def test_filter(loader):
    spec = Input(
        name="vehicle",
        from_=Vehicle,
        where=Filter(Vehicle, "veh_id", Equal("veh_1")),
        select=Fields("veh_id", "soc"),
    )
    result = loader.load_inputs(spec, TimeWindow(0.0, 10.0))
    assert len(result["vehicle"]) == 2  # veh_1 at t=1.0 and t=2.5
    assert all(row["veh_id"] == "veh_1" for row in result["vehicle"])


def test_filter_without_explicit_record(loader):
    """Filter(field, condition) omits the record and must fall back to
    Input.from_, matching validate_where's fallback behavior."""
    spec = Input(
        name="vehicle",
        from_=Vehicle,
        where=Filter("veh_id", Equal("veh_1")),
        select=Fields("veh_id", "soc"),
    )
    result = loader.load_inputs(spec, TimeWindow(0.0, 10.0))
    assert len(result["vehicle"]) == 2  # veh_1 at t=1.0 and t=2.5
    assert all(row["veh_id"] == "veh_1" for row in result["vehicle"])


def test_filter_in(loader):
    spec = Input(
        name="vehicle",
        from_=Vehicle,
        where=Filter(Vehicle, "veh_id", In(["veh_1", "veh_3"])),
        select=Fields("veh_id", "soc"),
    )
    result = loader.load_inputs(spec, TimeWindow(0.0, 10.0))
    assert sorted(row["veh_id"] for row in result["vehicle"]) == [
        "veh_1",
        "veh_1",
        "veh_3",
    ]


def test_filter_not_in(loader):
    spec = Input(
        name="vehicle",
        from_=Vehicle,
        where=Filter(Vehicle, "veh_id", NotIn(["veh_1", "veh_3"])),
        select=Fields("veh_id", "soc"),
    )
    result = loader.load_inputs(spec, TimeWindow(0.0, 10.0))
    assert [row["veh_id"] for row in result["vehicle"]] == ["veh_2"]


@pytest.fixture
def spatial_loader(postgresql):
    cur = postgresql.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    cur.execute(f"CREATE SCHEMA {RUN_ID}")
    cur.execute(
        f"CREATE TABLE {RUN_ID}.vehicle_location (veh_id TEXT, time_s FLOAT, coords geometry)"
    )
    cur.execute(f"CREATE TABLE {RUN_ID}.zone (zone_id TEXT, coords geometry)")
    cur.execute(
        f"INSERT INTO {RUN_ID}.vehicle_location (veh_id, time_s, coords) VALUES "
        "('veh_1', 1.0, ST_GeomFromText('POINT(2 2)', 4326)), "
        "('veh_2', 1.0, ST_GeomFromText('POINT(7 7)', 4326)), "
        "('veh_3', 1.0, ST_GeomFromText('POINT(20 20)', 4326))"
    )
    cur.execute(
        f"INSERT INTO {RUN_ID}.zone (zone_id, coords) VALUES "
        "('z1', ST_GeomFromText('POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))', 4326)), "
        "('z2', ST_GeomFromText('POLYGON((5 5, 15 5, 15 15, 5 15, 5 5))', 4326))"
    )
    postgresql.commit()
    return InputLoader(FakePool(postgresql), RUN_ID)


def test_spatial_join_fans_out(spatial_loader):
    spec = Input(
        name="vehicle_location",
        from_=VehicleLocation,
        on=SpatialJoin(VehicleLocation, Zone, predicate="exact"),
        select=Fields((VehicleLocation, "veh_id"), (Zone, "zone_id")),
    )
    result = spatial_loader.load_inputs(spec, TimeWindow(0.0, 2.0))
    rows = result["vehicle_location"]
    # veh_1 only intersects z1; veh_2 intersects both z1 and z2; veh_3 intersects neither.
    assert len(rows) == 3
    matches = {row["veh_id"] for row in rows}
    assert matches == {"veh_1", "veh_2"}
    assert sum(row["veh_id"] == "veh_2" for row in rows) == 2


def test_spatial_join_nearest(spatial_loader):
    spec = Input(
        name="vehicle_location",
        from_=VehicleLocation,
        on=SpatialJoin(VehicleLocation, Zone, predicate="nearest"),
        select=Fields((VehicleLocation, "veh_id"), (Zone, "zone_id")),
    )
    result = spatial_loader.load_inputs(spec, TimeWindow(0.0, 2.0))
    rows = result["vehicle_location"]
    # Unlike "exact", every vehicle gets exactly one match, including veh_3
    # (20,20), which intersects neither zone but is closer to z2 than z1.
    assert len(rows) == 3
    matches = {row["veh_id"]: row["zone_id"] for row in rows}
    assert matches["veh_3"] == "z2"
    assert matches["veh_1"] == "z1"


@pytest.mark.parametrize("join_type, expected_rows", [("inner", 2), ("left", 3)])
def test_spatial_join_nearest_max_distance(spatial_loader, join_type, expected_rows):
    spec = Input(
        name="vehicle_location",
        from_=VehicleLocation,
        on=SpatialJoin(
            VehicleLocation,
            Zone,
            join_type=join_type,
            predicate="nearest",
            max_distance=1000,
        ),
        select=Fields((VehicleLocation, "veh_id"), (Zone, "zone_id")),
    )
    result = spatial_loader.load_inputs(spec, TimeWindow(0.0, 2.0))
    rows = result["vehicle_location"]
    matches = {row["veh_id"]: row["zone_id"] for row in rows}
    # veh_1 and veh_2 lie inside a zone. veh_3 is hundreds of km from z2, so it
    # has no zone within 1 km: an inner join drops it, a left join keeps it
    # without one.
    assert len(rows) == expected_rows
    assert matches["veh_1"] == "z1"
    assert matches["veh_2"] in ("z1", "z2")
    assert matches.get("veh_3") is None


def test_spatial_join_nearest_where(spatial_loader):
    spec = Input(
        name="vehicle_location",
        from_=VehicleLocation,
        on=SpatialJoin(
            VehicleLocation,
            Zone,
            predicate="nearest",
            where=Filter(Zone, "zone_id", Equal("z2")),
        ),
        select=Fields((VehicleLocation, "veh_id"), (Zone, "zone_id")),
    )
    rows = spatial_loader.load_inputs(spec, TimeWindow(0.0, 2.0))["vehicle_location"]
    # The filter applies while matching: veh_1 lies in z1 but gets z2, the
    # nearest zone that passes it, instead of being dropped.
    assert {row["veh_id"]: row["zone_id"] for row in rows} == {
        "veh_1": "z2",
        "veh_2": "z2",
        "veh_3": "z2",
    }


def test_spatial_join_exact_where(spatial_loader):
    spec = Input(
        name="vehicle_location",
        from_=VehicleLocation,
        on=SpatialJoin(
            VehicleLocation,
            Zone,
            predicate="exact",
            where=Filter("zone_id", Equal("z2")),
        ),
        select=Fields((VehicleLocation, "veh_id"), (Zone, "zone_id")),
    )
    rows = spatial_loader.load_inputs(spec, TimeWindow(0.0, 2.0))["vehicle_location"]
    # veh_2 intersects both zones but only its z2 match remains; veh_1 only
    # intersects z1, so the inner join drops it.
    assert [(row["veh_id"], row["zone_id"]) for row in rows] == [("veh_2", "z2")]


def test_spatial_join_where_values_precede_input_filters(spatial_loader):
    spec = Input(
        name="vehicle_location",
        from_=VehicleLocation,
        where=Filter(VehicleLocation, "veh_id", Equal("veh_3")),
        on=SpatialJoin(
            VehicleLocation,
            Zone,
            predicate="nearest",
            where=Filter(Zone, "zone_id", Equal("z1")),
        ),
        select=Fields((VehicleLocation, "veh_id"), (Zone, "zone_id")),
    )
    rows = spatial_loader.load_inputs(spec, TimeWindow(0.0, 2.0))["vehicle_location"]
    assert [(row["veh_id"], row["zone_id"]) for row in rows] == [("veh_3", "z1")]


def test_join(loader):
    spec = Input(
        name="vehicle",
        from_=Vehicle,
        on=KeyJoin((Vehicle, "veh_id"), (Battery, "veh_id")),
        select=Fields((Vehicle, "veh_id", "soc"), (Battery, "capacity")),
    )
    result = loader.load_inputs(spec, TimeWindow(1.0, 2.0))
    assert len(result["vehicle"]) == 1
    assert result["vehicle"][0]["veh_id"] == "veh_1"
    assert result["vehicle"][0]["soc"] == 0.5
    assert result["vehicle"][0]["capacity"] == 100.0


def test_join_fans_out(loader, postgresql):
    cur = postgresql.cursor()
    cur.execute(
        f"INSERT INTO {RUN_ID}.battery (veh_id, capacity) VALUES ('veh_1', 150.0)"
    )
    postgresql.commit()
    spec = Input(
        name="vehicle",
        from_=Vehicle,
        on=KeyJoin((Vehicle, "veh_id"), (Battery, "veh_id")),
        select=Fields((Vehicle, "veh_id", "soc"), (Battery, "capacity")),
    )
    result = loader.load_inputs(spec, TimeWindow(1.0, 2.0))
    # veh_1 now has two battery rows, so the join fans out.
    assert len(result["vehicle"]) == 2
    assert {row["capacity"] for row in result["vehicle"]} == {100.0, 150.0}


# Rows are inserted out of order on purpose: without ORDER BY, Postgres would
# usually return them in insertion order.


def test_window_rows_are_ordered_by_time_then_key(loader, postgresql):
    cur = postgresql.cursor()
    cur.executemany(
        f"INSERT INTO {RUN_ID}.vehicle (veh_id, soc, time_s) VALUES (%s, %s, %s)",
        [("veh_9", 0.1, 5.0), ("veh_5", 0.2, 5.0), ("veh_7", 0.3, 4.0)],
    )
    postgresql.commit()
    spec = Input(name="vehicle", from_=Vehicle, select=Fields("veh_id"))
    rows = loader.load_inputs(spec, TimeWindow(4.0, 6.0))["vehicle"]
    assert [row["veh_id"] for row in rows] == ["veh_7", "veh_5", "veh_9"]


@pytest.fixture
def zone_added_last(spatial_loader, postgresql):
    # z0 also contains veh_2 (7, 7), but comes last in the table.
    cur = postgresql.cursor()
    cur.execute(
        f"INSERT INTO {RUN_ID}.zone (zone_id, coords) VALUES "
        "('z0', ST_GeomFromText('POLYGON((6 6, 8 6, 8 8, 6 8, 6 6))', 4326))"
    )
    postgresql.commit()
    return spatial_loader


def test_join_fan_out_is_ordered_by_joined_key(zone_added_last):
    spec = Input(
        name="vehicle_location",
        from_=VehicleLocation,
        on=SpatialJoin(VehicleLocation, Zone, predicate="exact"),
        select=Fields((VehicleLocation, "veh_id"), (Zone, "zone_id")),
    )
    rows = zone_added_last.load_inputs(spec, TimeWindow(0.0, 2.0))["vehicle_location"]
    assert [(row["veh_id"], row["zone_id"]) for row in rows] == [
        ("veh_1", "z1"),
        ("veh_2", "z0"),
        ("veh_2", "z1"),
        ("veh_2", "z2"),
    ]


@pytest.fixture
def trip_loader(loader, postgresql):
    cur = postgresql.cursor()
    cur.execute(
        f"CREATE TABLE {RUN_ID}.trip (veh_id TEXT, start_s FLOAT, end_s FLOAT)"
    )
    cur.executemany(
        f"INSERT INTO {RUN_ID}.trip (veh_id, start_s, end_s) VALUES (%s, %s, %s)",
        [
            ("veh_5", 5.0, None),  # still running
            ("veh_1", 0.0, 5.0),  # ended before the window
            ("veh_2", 0.0, 15.0),  # ends during it
            ("veh_3", 12.0, 18.0),  # entirely inside it
            ("veh_4", 20.0, None),  # starts when it ends
            ("veh_6", None, None),  # always there
            ("veh_7", 0.0, 10.0),  # ends when it starts
        ],
    )
    postgresql.commit()
    return loader


def test_entities_active_during_window(trip_loader):
    spec = Input(name="trip", from_=Trip, select=Fields("veh_id"))
    rows = trip_loader.load_inputs(spec, TimeWindow(10.0, 20.0))["trip"]
    assert [row["veh_id"] for row in rows] == ["veh_2", "veh_3", "veh_5", "veh_6"]


def test_entity_filter(trip_loader):
    spec = Input(
        name="trip",
        from_=Trip,
        where=Filter("veh_id", In(("veh_2", "veh_4"))),
        select=Fields("veh_id"),
    )
    rows = trip_loader.load_inputs(spec, TimeWindow(10.0, 20.0))["trip"]
    assert [row["veh_id"] for row in rows] == ["veh_2"]


def test_entity_join(trip_loader):
    spec = Input(
        name="trip",
        from_=Trip,
        on=KeyJoin((Trip, "veh_id"), (Battery, "veh_id")),
        select=Fields((Trip, "veh_id"), (Battery, "capacity")),
    )
    rows = trip_loader.load_inputs(spec, TimeWindow(10.0, 20.0))["trip"]
    assert [(row["veh_id"], row["capacity"]) for row in rows] == [
        ("veh_2", 200.0),
        ("veh_3", 300.0),
    ]
