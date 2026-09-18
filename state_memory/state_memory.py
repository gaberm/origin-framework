import io
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from typing import Sequence
import psycopg2.pool
from base import Input, Record
from base.output.geometry import Geometry, to_ewkt
from state_memory.validation import validate_run_id
from supervisory.scheduling.time_window import TimeWindow
from .input_loader import InputLoader
from .schema_manager import SchemaManager, COORDS_FIELDS


def _generate_run_id() -> str:
    return datetime.now().strftime("run_%Y%m%d_%H%M%S")


_COPY_NULL = "\\N"
_COPY_TEXT_ESCAPES = str.maketrans(
    {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
)


def _array_literal(values: list) -> str:
    """A Postgres array literal with every element quoted and escaped."""
    items = (
        (
            "NULL"
            if value is None
            else '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
        )
        for value in values
    )
    return "{" + ",".join(items) + "}"


class StateMemory:
    def __init__(self, db_url: str, run_id: str = None):
        self.run_id = run_id or _generate_run_id()
        validate_run_id(self.run_id)

        self._pool = psycopg2.pool.ThreadedConnectionPool(1, 5, db_url)
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._background_writes: list[Future] = []

        self._schema_conn = self._pool.getconn()

        self._schema = SchemaManager(self._schema_conn, self.run_id)
        self._loader = InputLoader(self._pool, self.run_id)

    @contextmanager
    def _connection(self):
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def setup(self, entity_classes: list[type[Record]]):
        self._schema.setup(entity_classes)

    def insert_outputs(self, entity_cls: type[Record], rows: list[dict]):
        self._raise_failed_background_writes()
        if not rows:
            return
        columns, geometry_columns = self._resolve_columns(entity_cls, rows[0])
        buf = self._prepare_copy_payload(rows, columns, geometry_columns)
        if entity_cls.diagnostic:
            self._background_writes.append(
                self._executor.submit(self._write_records, entity_cls, columns, buf)
            )
        else:
            self._write_records(entity_cls, columns, buf)

    def _raise_failed_background_writes(self):
        finished, pending = [], []
        for future in self._background_writes:
            (finished if future.done() else pending).append(future)
        self._background_writes = pending
        for future in finished:
            if future.exception() is not None:
                raise RuntimeError(
                    "a background write of diagnostic records failed"
                ) from future.exception()

    def _write_records(
        self, entity_cls: type[Record], columns: list[str], buf: io.StringIO
    ):
        table = f"{self.run_id}.{entity_cls.table_name}"
        staging = f"staging_{entity_cls.table_name}"
        column_list = ", ".join(columns)
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE TEMP TABLE IF NOT EXISTS {staging} "
                    f"(LIKE {table}, _row bigserial) ON COMMIT DELETE ROWS"
                )
                cur.copy_expert(f"COPY {staging} ({column_list}) FROM STDIN", buf)
                cur.execute(
                    self._move_staged_rows_query(entity_cls, columns, table, staging)
                )
            conn.commit()

    @staticmethod
    def _move_staged_rows_query(
        entity_cls: type[Record], columns: list[str], table: str, staging: str
    ) -> str:
        column_list = ", ".join(columns)
        primary_key = getattr(entity_cls, "primary_key", ())
        if not primary_key:
            return (
                f"INSERT INTO {table} ({column_list}) "
                f"SELECT {column_list} FROM {staging}"
            )
        key_list = ", ".join(primary_key)
        updated = [col for col in columns if col not in primary_key]
        if entity_cls.on_conflict == "UPDATE" and updated:
            select = (
                f"SELECT DISTINCT ON ({key_list}) {column_list} FROM {staging} "
                f"ORDER BY {key_list}, _row DESC"
            )
            assignments = ", ".join(f"{col} = EXCLUDED.{col}" for col in updated)
            action = f"DO UPDATE SET {assignments}"
        else:
            select = f"SELECT {column_list} FROM {staging} ORDER BY _row"
            action = "DO NOTHING"
        return (
            f"INSERT INTO {table} ({column_list}) {select} "
            f"ON CONFLICT ({key_list}) {action}"
        )

    @staticmethod
    def _resolve_columns(
        entity_cls: type[Record], first_row: dict
    ) -> tuple[list[str], set[str]]:
        columns = list(first_row.keys())
        geometry_columns = (
            {col for col in columns if col in COORDS_FIELDS}
            if issubclass(entity_cls, Geometry)
            else set()
        )
        return columns, geometry_columns

    @classmethod
    def _prepare_copy_payload(
        cls, rows: list[dict], columns: list[str], geometry_columns: set[str]
    ) -> io.StringIO:
        buf = io.StringIO()
        for row in rows:
            buf.write(
                "\t".join(
                    cls._serialize_value(row, col, geometry_columns) for col in columns
                )
            )
            buf.write("\n")
        buf.seek(0)
        return buf

    @staticmethod
    def _serialize_value(row: dict, col: str, geometry_columns: set[str]) -> str:
        value = row[col]
        if value is None:
            return _COPY_NULL
        if col in geometry_columns:
            srid = row["srid"] if col == "coords" else 4326
            value = to_ewkt(value, row["shape"], srid)
        elif isinstance(value, list):
            value = _array_literal(value)
        return str(value).translate(_COPY_TEXT_ESCAPES)

    def _execute_copy(self, query: str, buf: io.StringIO):
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.copy_expert(query, buf)
            conn.commit()

    def load_inputs(
        self,
        input_specs: Input | Sequence[Input],
        time_interval: TimeWindow,
    ) -> dict[str, list[dict]]:
        return self._loader.load_inputs(input_specs, time_interval)

    def reset_tables(self, drop_tables: bool = False):
        self._schema.reset_tables(drop_tables=drop_tables)

    def delete_run(self, run_id: str):
        self._schema.delete_run(run_id)

    def insert_external_dataset(self, dataset) -> None:
        self._schema.setup_external_table(dataset)
        if not dataset.data:
            return
        columns = list(dataset.data.keys())
        n_rows = len(next(iter(dataset.data.values())))
        rows = [{col: dataset.data[col][i] for col in columns} for i in range(n_rows)]
        buf = self._prepare_copy_payload(rows, columns, geometry_columns=set())
        query = (
            f"COPY {self.run_id}.{dataset.table_name} "
            f"({', '.join(columns)}) FROM STDIN"
        )
        self._execute_copy(query, buf)

    def close_conn(self):
        try:
            self._executor.shutdown(wait=True)
            self._raise_failed_background_writes()
        finally:
            self._pool.putconn(self._schema_conn)
            self._pool.closeall()
