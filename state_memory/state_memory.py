from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from typing import Sequence
import psycopg2.pool
from psycopg2.extras import execute_values
from base import Input, Record
from state_memory.validation import validate_run_id
from supervisory.scheduling.time_window import TimeWindow
from .input_loader import InputLoader
from .schema_manager import SchemaManager


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
        argslist = [tuple(row[col] for col in columns) for row in rows]
        query = (
            f"INSERT INTO {self.run_id}.{entity_cls.table_name} "
            f"({', '.join(columns)}) VALUES %s"
        )
        if entity_cls.diagnostic:
            self._executor.submit(self._execute_insert, query, argslist)
        else:
            self._execute_insert(query, argslist)

    def _execute_insert(self, query: str, argslist: list[tuple]):
        # One connection checkout, one multi-row statement, one commit per batch.
        with self._connection() as conn:
            with conn.cursor() as cur:
                execute_values(cur, query, argslist)
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
