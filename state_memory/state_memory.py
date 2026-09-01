from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from typing import Sequence
import psycopg2.pool
from psycopg2.extras import execute_values
from base import Input, Record
from base.output.geometry import Geometry, to_ewkt
from state_memory.validation import validate_run_id
from supervisory.scheduling.time_window import TimeWindow
from .input_loader import InputLoader
from .schema_manager import SchemaManager, COORDS_FIELDS


def _generate_run_id() -> str:
    return datetime.now().strftime("run_%Y%m%d_%H%M%S")


class StateMemory:
    def __init__(self, db_url: str, run_id: str = None):
        self.run_id = run_id or _generate_run_id()
        validate_run_id(self.run_id)

        self._pool = psycopg2.pool.ThreadedConnectionPool(1, 5, db_url)
        self._executor = ThreadPoolExecutor(max_workers=2)

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
        if not rows:
            return
        # Fix the column order once so every row serializes consistently.
        columns = list(rows[0].keys())
        geometry_columns = (
            {col for col in columns if col in COORDS_FIELDS}
            if issubclass(entity_cls, Geometry)
            else set()
        )
        argslist = [
            tuple(self._serialize_value(row, col, geometry_columns) for col in columns)
            for row in rows
        ]
        template = "(" + ", ".join(
            "ST_GeomFromEWKT(%s)" if col in geometry_columns else "%s"
            for col in columns
        ) + ")"
        query = (
            f"INSERT INTO {self.run_id}.{entity_cls.table_name} "
            f"({', '.join(columns)}) VALUES %s"
        )
        if entity_cls.diagnostic:
            self._executor.submit(self._execute_insert, query, argslist, template)
        else:
            self._execute_insert(query, argslist, template)

    @staticmethod
    def _serialize_value(row: dict, col: str, geometry_columns: set[str]):
        if col not in geometry_columns or row[col] is None:
            return row[col]
        srid = row["srid"] if col == "coords" else 4326
        return to_ewkt(row[col], row["shape"], srid)

    def _execute_insert(self, query: str, argslist: list[tuple], template: str = None):
        # One connection checkout, one multi-row statement, one commit per batch.
        with self._connection() as conn:
            with conn.cursor() as cur:
                execute_values(cur, query, argslist, template=template)
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
        columns = list(dataset.data.keys())
        n_rows = len(next(iter(dataset.data.values()))) if dataset.data else 0
        for i in range(n_rows):
            row = {col: dataset.data[col][i] for col in columns}
            fields = list(row.keys())
            values = list(row.values())
            query = (
                f"INSERT INTO {self.run_id}.{dataset.table_name} "
                f"({', '.join(fields)}) VALUES ({', '.join(['%s'] * len(fields))})"
            )
            self._execute_insert(query, values)

    def close_conn(self):
        self._executor.shutdown(wait=True)
        self._pool.putconn(self._schema_conn)
        self._pool.closeall()
