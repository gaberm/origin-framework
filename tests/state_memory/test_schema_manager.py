import pytest
from state_memory.schema_manager import SchemaManager
from dataclasses import dataclass
from base import Record, Geometry, SHAPE_TYPE
from nashville.outputs.charging import ChargingEvent, PortStatus, Station
from nashville.outputs.sumo import EV, VehicleBattery
from _records import Vehicle, Battery, RUN_ID


@dataclass(kw_only=True)
class Cell(Record, Geometry):
    table_name = "cell"
    primary_key = ("cell_id",)
    indexed = ("h3_ids", "label")
    cell_id: str
    label: str
    shape: SHAPE_TYPE = "POINT"


@pytest.fixture
def schema_manager(postgresql):
    return SchemaManager(postgresql, RUN_ID)


def test_setup_same_schema_twice(schema_manager):
    schema_manager.setup([Vehicle])
    with pytest.raises(
        RuntimeError, match=f"A simulation run named '{RUN_ID}' already exists"
    ):
        schema_manager.setup([Vehicle])


def test_setup_creates_tables(schema_manager):
    schema_manager.setup([Vehicle, Battery])
    cur = schema_manager.conn.cursor()
    cur.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = %s ORDER BY table_name, column_name",
        (RUN_ID,),
    )
    rows = cur.fetchall()
    assert ("vehicle", "veh_id") in rows
    assert ("vehicle", "soc") in rows
    assert ("vehicle", "time") in rows
    assert ("battery", "veh_id") in rows
    assert ("battery", "capacity") in rows


def test_insert_time_field(schema_manager):
    """The time filed can be of type float or int."""
    schema_manager.setup([Vehicle])
    cur = schema_manager.conn.cursor()
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'vehicle' AND column_name = 'time'",
        (RUN_ID,),
    )
    assert cur.fetchone()[0] == "double precision"


def test_reset_tables(schema_manager):
    schema_manager.setup([Vehicle, Battery])
    cur = schema_manager.conn.cursor()
    cur.execute(
        f"INSERT INTO {RUN_ID}.vehicle (veh_id, soc, time) VALUES ('v1', 0.5, 1.0)"
    )
    schema_manager.conn.commit()
    schema_manager.reset_tables()
    cur.execute(f"SELECT COUNT(*) FROM {RUN_ID}.vehicle")
    assert cur.fetchone() == (0,)
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (RUN_ID,),
    )
    assert ("vehicle",) in cur.fetchall()


def test_reset_tables_drops_tables(schema_manager):
    schema_manager.setup([Vehicle, Battery])
    schema_manager.reset_tables(drop_tables=True)
    cur = schema_manager.conn.cursor()
    cur.execute(
        "SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s",
        (RUN_ID,),
    )
    assert cur.fetchone() == (RUN_ID,)
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (RUN_ID,),
    )
    assert cur.fetchall() == []


def test_delete_run(schema_manager):
    schema_manager.setup([Vehicle, Battery])
    schema_manager.delete_run(RUN_ID)
    cur = schema_manager.conn.cursor()
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (RUN_ID,),
    )
    assert cur.fetchall() == []


def test_insert_rows(schema_manager):
    schema_manager.setup([Vehicle])
    cur = schema_manager.conn.cursor()
    cur.executemany(
        f"INSERT INTO {RUN_ID}.vehicle (veh_id, soc, time) VALUES (%s, %s, %s)",
        [("v1", 0.5, 1.0), ("v2", 0.8, 2.0)],
    )
    schema_manager.conn.commit()
    cur.execute(f"SELECT veh_id, soc FROM {RUN_ID}.vehicle ORDER BY time")
    assert cur.fetchall() == [("v1", 0.5), ("v2", 0.8)]


def test_list_fields_are_arrays_with_gin_index(schema_manager):
    schema_manager.setup([Cell])
    cur = schema_manager.conn.cursor()
    cur.execute(
        "SELECT data_type, udt_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'cell' AND column_name = 'h3_ids'",
        (RUN_ID,),
    )
    assert cur.fetchone() == ("ARRAY", "_text")
    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s "
        "AND tablename = 'cell' ORDER BY indexname",
        (RUN_ID,),
    )
    indexes = dict(cur.fetchall())
    assert "USING gin (h3_ids)" in indexes["idx_cell_h3_ids"]
    assert "USING btree (label)" in indexes["idx_cell_label"]


def _indexes(conn, table):
    cur = conn.cursor()
    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s "
        "AND tablename = %s",
        (RUN_ID, table),
    )
    return dict(cur.fetchall())


def test_time_field_is_indexed_automatically(schema_manager):
    schema_manager.setup([Vehicle])
    indexes = _indexes(schema_manager.conn, "vehicle")
    assert "USING btree (\"time\")" in indexes["idx_vehicle_time"]
    # veh_id is served by the primary key, so it gets no index of its own.
    assert set(indexes) == {"vehicle_pkey", "idx_vehicle_time"}


def test_nashville_indexes(schema_manager):
    schema_manager.setup([EV, VehicleBattery, Station, PortStatus, ChargingEvent])
    conn = schema_manager.conn
    assert set(_indexes(conn, "ev")) == {
        "ev_pkey", "idx_ev_time", "idx_ev_h3_ids", "idx_ev_state",
        "idx_ev_coords_gist", "idx_ev_coords_4326_gist",
    }
    assert set(_indexes(conn, "vehicle_battery")) == {"vehicle_battery_pkey"}
    assert set(_indexes(conn, "port_status")) == {
        "port_status_pkey", "idx_port_status_time", "idx_port_status_station_id",
    }
    assert set(_indexes(conn, "charging_events")) == {
        "charging_events_pkey", "idx_charging_events_time",
        "idx_charging_events_driver_id", "idx_charging_events_tour_id",
        "idx_charging_events_station_id",
    }
