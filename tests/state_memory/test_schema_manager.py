import pytest
from state_memory.schema_manager import SchemaManager
from _records import Vehicle, Battery, RUN_ID


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
