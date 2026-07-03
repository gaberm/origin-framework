import pytest
from base import Input, Fields, Filter, Equal, Join, Latest
from state_memory.input_loader import InputLoader
from supervisory.scheduling.time_window import TimeWindow
from _records import Vehicle, Battery, RUN_ID


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
    cur.execute(f"CREATE TABLE {RUN_ID}.vehicle (veh_id TEXT, soc FLOAT, time FLOAT)")
    cur.execute(f"CREATE TABLE {RUN_ID}.battery (veh_id TEXT, capacity FLOAT)")
    cur.executemany(
        f"INSERT INTO {RUN_ID}.vehicle (veh_id, soc, time) VALUES (%s, %s, %s)",
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
    Input.from_, matching Input._validate_where's fallback behavior."""
    spec = Input(
        name="vehicle",
        from_=Vehicle,
        where=Filter("veh_id", Equal("veh_1")),
        select=Fields("veh_id", "soc"),
    )
    result = loader.load_inputs(spec, TimeWindow(0.0, 10.0))
    assert len(result["vehicle"]) == 2  # veh_1 at t=1.0 and t=2.5
    assert all(row["veh_id"] == "veh_1" for row in result["vehicle"])


def test_join(loader):
    spec = Input(
        name="vehicle",
        from_=Vehicle,
        on=Join((Vehicle, "veh_id"), (Battery, "veh_id")),
        select=Fields((Vehicle, "veh_id", "soc"), (Battery, "capacity")),
    )
    result = loader.load_inputs(spec, TimeWindow(1.0, 2.0))
    assert len(result["vehicle"]) == 1
    assert result["vehicle"][0]["veh_id"] == "veh_1"
    assert result["vehicle"][0]["soc"] == 0.5
    assert result["vehicle"][0]["capacity"] == 100.0
