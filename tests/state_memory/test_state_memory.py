import psycopg2
import pytest
from unittest.mock import patch
from dataclasses import dataclass, replace
from base import Entity, Observation, Record, Timestamp, ref
from state_memory.schema_manager import SchemaManager
from state_memory.state_memory import StateMemory, _storage_order
from _records import Trip, Vehicle, VehicleLocation, RUN_ID


@dataclass(kw_only=True)
class UpdatedVehicle(Record, Timestamp):
    table_name = "updated_vehicle"
    primary_key = ("veh_id",)
    on_conflict = "update"
    veh_id: str
    soc: float


@dataclass(kw_only=True)
class Note(Record, Timestamp):
    table_name = "note"
    primary_key = ("note_id",)
    note_id: str
    text: str | None
    tags: list[str] | None


@dataclass(kw_only=True)
class TripPosition(Observation):
    id_field = "veh_id"
    entity = Trip
    veh_id: str


@dataclass(kw_only=True)
class TripStart(Observation):
    id_field = "veh_id"
    observation_type = "start"
    entity = Trip
    veh_id: str


@dataclass(kw_only=True)
class TripEnd(Observation):
    id_field = "veh_id"
    observation_type = "end"
    entity = Trip
    veh_id: str


@dataclass(kw_only=True)
class Driver(Entity):
    id_field = "driver_id"
    driver_id: str


@dataclass(kw_only=True)
class Car(Entity):
    id_field = "veh_id"
    veh_id: str
    driver_id: str | None = ref(Driver)


def _dsn(postgresql) -> str:
    info = postgresql.info
    return f"host={info.host} port={info.port} user={info.user} dbname={info.dbname}"


@pytest.fixture
def state_memory(postgresql):
    SchemaManager(postgresql, RUN_ID).setup(
        [
            Vehicle,
            UpdatedVehicle,
            VehicleLocation,
            Note,
            Trip,
            TripPosition,
            TripStart,
            TripEnd,
            Car,
            Driver,
        ]
    )
    sm = StateMemory(_dsn(postgresql), RUN_ID)
    yield sm
    sm.close_conn()


def _rows(postgresql, table):
    cur = postgresql.cursor()
    cur.execute(f"SELECT veh_id, soc, time_s FROM {RUN_ID}.{table} ORDER BY veh_id")
    rows = cur.fetchall()
    postgresql.commit()
    return rows


def test_insert_records(state_memory, postgresql):
    state_memory.insert_records(
        [
            Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0),
            Vehicle(veh_id="veh_2", soc=0.8, time_s=2.0),
        ],
    )
    cur = postgresql.cursor()
    cur.execute(f"SELECT veh_id, soc FROM {RUN_ID}.vehicle ORDER BY time_s")
    assert cur.fetchall() == [("veh_1", 0.5), ("veh_2", 0.8)]


def test_ignore_keeps_existing_and_first_duplicate(state_memory, postgresql):
    state_memory.insert_records([Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0)])
    state_memory.insert_records(
        [
            Vehicle(veh_id="veh_1", soc=0.9, time_s=2.0),  # already stored
            Vehicle(veh_id="veh_2", soc=0.6, time_s=2.0),
            Vehicle(veh_id="veh_2", soc=0.7, time_s=3.0),  # duplicate in batch
        ],
    )
    assert _rows(postgresql, "vehicle") == [("veh_1", 0.5, 1.0), ("veh_2", 0.6, 2.0)]


def test_update_overwrites_with_last_duplicate(state_memory, postgresql):
    state_memory.insert_records([UpdatedVehicle(veh_id="veh_1", soc=0.5, time_s=1.0)])
    state_memory.insert_records(
        [
            UpdatedVehicle(veh_id="veh_1", soc=0.9, time_s=2.0),  # already stored
            UpdatedVehicle(veh_id="veh_2", soc=0.6, time_s=2.0),
            UpdatedVehicle(veh_id="veh_2", soc=0.7, time_s=3.0),  # duplicate in batch
        ],
    )
    assert _rows(postgresql, "updated_vehicle") == [
        ("veh_1", 0.9, 2.0),
        ("veh_2", 0.7, 3.0),
    ]


def test_geometry_rows_go_through_staging(state_memory, postgresql):
    location = VehicleLocation(veh_id="veh_1", coords=(-86.7, 36.1), time_s=1.0)
    state_memory.insert_records([location, replace(location, time_s=2.0)])
    cur = postgresql.cursor()
    cur.execute(
        f"SELECT veh_id, time_s, ST_AsText(coords), ST_SRID(coords), h3_ids "
        f"FROM {RUN_ID}.vehicle_location"
    )
    assert cur.fetchall() == [
        ("veh_1", 1.0, "POINT(-86.7 36.1)", 4326, ["89264d10dabffff"])
    ]


def test_geometry_coords_are_stored_in_4326(state_memory, postgresql):
    location = VehicleLocation(
        veh_id="veh_1",
        coords=(-9651399.851776818, 4314389.956334436),
        epsg_code=3857,
        time_s=1.0,
    )
    state_memory.insert_records([location])
    cur = postgresql.cursor()
    cur.execute(
        f"SELECT ST_X(coords), ST_Y(coords), ST_SRID(coords), h3_ids "
        f"FROM {RUN_ID}.vehicle_location"
    )
    x, y, srid, h3_ids = cur.fetchone()
    assert (x, y) == pytest.approx((-86.7, 36.1))
    assert (srid, h3_ids) == (4326, ["89264d10dabffff"])


def test_geometry_tables_have_no_epsg_code(state_memory, postgresql):
    cur = postgresql.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'vehicle_location'",
        (RUN_ID,),
    )
    columns = {row[0] for row in cur.fetchall()}
    postgresql.commit()
    assert "epsg_code" not in columns
    assert {"coords", "h3_ids"} <= columns


def test_diagnostic_records_use_same_conflict_handling(state_memory, postgresql):
    @dataclass(kw_only=True)
    class DiagnosticVehicle(Vehicle):
        table_name = "vehicle"
        diagnostic = True

    state_memory.insert_records(
        [
            DiagnosticVehicle(veh_id="veh_1", soc=0.5, time_s=1.0),
            DiagnosticVehicle(veh_id="veh_1", soc=0.9, time_s=2.0),
        ]
    )
    state_memory._executor.shutdown(wait=True)
    assert _rows(postgresql, "vehicle") == [("veh_1", 0.5, 1.0)]


def _trip_starts(postgresql):
    cur = postgresql.cursor()
    cur.execute(f"SELECT veh_id, start_s FROM {RUN_ID}.trip ORDER BY veh_id")
    rows = cur.fetchall()
    postgresql.commit()
    return rows


def _trip_ends(postgresql):
    cur = postgresql.cursor()
    cur.execute(f"SELECT veh_id, end_s FROM {RUN_ID}.trip ORDER BY veh_id")
    rows = cur.fetchall()
    postgresql.commit()
    return rows


def test_end_observation_sets_entity_end(state_memory, postgresql):
    state_memory.insert_records(
        [
            Trip(veh_id="veh_1", start_s=0.0, end_s=None),
            Trip(veh_id="veh_2", start_s=0.0, end_s=None),
            Trip(veh_id="veh_3", start_s=0.0, end_s=None),
        ],
    )
    state_memory.insert_records(
        [
            TripEnd(veh_id="veh_1", time_s=50.0),
            TripEnd(veh_id="veh_2", time_s=30.0),
            TripEnd(veh_id="veh_2", time_s=40.0),  # the latest end wins
        ],
    )
    assert _trip_ends(postgresql) == [
        ("veh_1", 50.0),
        ("veh_2", 40.0),
        ("veh_3", None),
    ]


def test_start_observation_sets_entity_start(state_memory, postgresql):
    state_memory.insert_records(
        [
            Trip(veh_id="veh_1", start_s=None, end_s=None),
            Trip(veh_id="veh_2", start_s=5.0, end_s=None),
            Trip(veh_id="veh_3", start_s=5.0, end_s=None),
        ],
    )
    state_memory.insert_records(
        [
            TripStart(veh_id="veh_1", time_s=10.0),
            TripStart(veh_id="veh_2", time_s=30.0),
            TripStart(veh_id="veh_2", time_s=20.0),  # the earliest start wins
        ],
    )
    assert _trip_starts(postgresql) == [
        ("veh_1", 10.0),
        ("veh_2", 20.0),
        ("veh_3", 5.0),
    ]


def test_other_observations_leave_entity_lifetime(state_memory, postgresql):
    state_memory.insert_records([Trip(veh_id="veh_1", start_s=0.0, end_s=None)])
    state_memory.insert_records([TripPosition(veh_id="veh_1", time_s=50.0)])
    assert _trip_starts(postgresql) == [("veh_1", 0.0)]
    assert _trip_ends(postgresql) == [("veh_1", None)]


def test_serialize_value_escapes():
    def serialize(value):
        return StateMemory._serialize_value({"x": value}, "x", set())

    assert serialize(None) == "\\N"
    assert serialize("") == ""
    assert serialize("\\N") == "\\\\N"  # the string, not NULL
    assert serialize("a\tb\nc\r") == "a\\tb\\nc\\r"
    assert serialize(True) == "True"
    assert serialize(1.5) == "1.5"
    assert serialize(["a,b", 'say "hi"', None, 3]) == (
        '{"a,b","say \\\\"hi\\\\"",NULL,"3"}'
    )


def test_text_and_arrays_round_trip(state_memory, postgresql):
    notes = [
        Note(note_id="empty", text="", tags=[], time_s=1.0),
        Note(note_id="null", text=None, tags=None, time_s=1.0),
        Note(
            note_id="tricky",
            text="tab\there\nnewline \\N back\\slash",
            tags=["a,b", 'say "hi"', "back\\slash", "{brace}", None],
            time_s=1.0,
        ),
    ]
    state_memory.insert_records(notes)
    cur = postgresql.cursor()
    cur.execute(f"SELECT note_id, text, tags FROM {RUN_ID}.note ORDER BY note_id")
    assert cur.fetchall() == [
        ("empty", "", []),
        ("null", None, None),
        (
            "tricky",
            "tab\there\nnewline \\N back\\slash",
            ["a,b", 'say "hi"', "back\\slash", "{brace}", None],
        ),
    ]


def _failing_write(*args, **kwargs):
    raise ValueError("disk full")


def test_failed_background_write_raises_on_next_insert(state_memory):
    @dataclass(kw_only=True)
    class DiagnosticVehicle(Vehicle):
        diagnostic = True

    vehicle = Vehicle(veh_id="veh_1", soc=0.5, time_s=1.0)
    with patch.object(StateMemory, "_write_records", _failing_write):
        state_memory.insert_records(
            [DiagnosticVehicle(veh_id="veh_1", soc=0.5, time_s=1.0)]
        )
        state_memory._background_writes[0].exception()  # wait for it
    with pytest.raises(RuntimeError, match="background write") as error:
        state_memory.insert_records([vehicle])
    assert isinstance(error.value.__cause__, ValueError)
    # Reported once; the next write goes through.
    state_memory.insert_records([vehicle])


def test_failed_background_write_raises_on_close(state_memory, postgresql):
    @dataclass(kw_only=True)
    class DiagnosticVehicle(Vehicle):
        diagnostic = True

    sm = StateMemory(_dsn(postgresql), RUN_ID)
    with patch.object(StateMemory, "_write_records", _failing_write):
        sm.insert_records([DiagnosticVehicle(veh_id="veh_1", soc=0.5, time_s=1.0)])
        with pytest.raises(RuntimeError, match="background write"):
            sm.close_conn()
    # The pool is closed anyway.
    assert sm._pool.closed


def test_ref_is_a_foreign_key(state_memory, postgresql):
    state_memory.insert_records([Driver(driver_id="d1", start_s=0.0)])
    state_memory.insert_records(
        [
            Car(veh_id="v1", driver_id="d1", start_s=0.0),
            Car(veh_id="v2", driver_id=None, start_s=0.0),
        ],
    )
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        state_memory.insert_records(
            [Car(veh_id="v3", driver_id="unknown", start_s=0.0)]
        )
    cur = postgresql.cursor()
    cur.execute(f"SELECT veh_id FROM {RUN_ID}.car ORDER BY veh_id")
    assert cur.fetchall() == [("v1",), ("v2",)]
    postgresql.commit()


def test_ref_without_target_table_has_no_foreign_key(postgresql):
    SchemaManager(postgresql, "run_cars_only").setup([Car])
    sm = StateMemory(_dsn(postgresql), "run_cars_only")
    try:
        sm.insert_records([Car(veh_id="v1", driver_id="d1", start_s=0.0)])
    finally:
        sm.close_conn()


def test_reset_tables_with_foreign_keys(state_memory, postgresql):
    state_memory.insert_records([Driver(driver_id="d1", start_s=0.0)])
    state_memory.insert_records([Car(veh_id="v1", driver_id="d1", start_s=0.0)])
    state_memory.reset_tables()
    cur = postgresql.cursor()
    cur.execute(f"SELECT count(*) FROM {RUN_ID}.driver")
    assert cur.fetchone() == (0,)
    postgresql.commit()


def test_insert_records_stores_referenced_entities_first(state_memory, postgresql):
    state_memory.insert_records(
        [
            Car(veh_id="v1", driver_id="d1", start_s=0.0),
            Driver(driver_id="d1", start_s=0.0),
        ]
    )
    cur = postgresql.cursor()
    cur.execute(f"SELECT veh_id, driver_id FROM {RUN_ID}.car")
    assert cur.fetchall() == [("v1", "d1")]
    postgresql.commit()


def test_insert_records_stores_observations_after_their_entity(
    state_memory, postgresql
):
    state_memory.insert_records(
        [TripEnd(veh_id="veh_1", time_s=50.0), Trip(veh_id="veh_1", start_s=0.0)]
    )
    assert _trip_ends(postgresql) == [("veh_1", 50.0)]


def test_storage_order_follows_reference_chains():
    @dataclass(kw_only=True)
    class Station(Entity):
        id_field = "station_id"
        station_id: str

    @dataclass(kw_only=True)
    class Port(Entity):
        id_field = "port_id"
        port_id: str
        station_id: str = ref(Station)

    @dataclass(kw_only=True)
    class Load(Observation):
        id_field = "port_id"
        entity = Port
        port_id: str

    assert _storage_order([Load, Port, Station]) == [Station, Port, Load]
