import dataclasses
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import psycopg2.pool
from adapters.data_adapter import ExternalDataset
from state_memory.validation import validate_run_id
from .schema_manager import SchemaManager
from records.model_output import ModelOutput


def _generate_run_id() -> str:
    return datetime.now().strftime("run_%Y%m%d_%H%M%S")


class StateMemory:
    @classmethod
    def from_config(cls, config) -> "StateMemory":
        from adapters import ModelAdapter

        record_classes = [
            ModelAdapter._registry[config.models[name].adapter].OutputType
            for name in config.models
        ]
        return cls(record_classes=record_classes, **config.db)

    def __init__(self, db_url: str, record_classes: list, run_id: str = None):
        self.run_id = run_id or _generate_run_id()
        validate_run_id(self.run_id)

        self._pool = psycopg2.pool.ThreadedConnectionPool(1, 5, db_url)
        self._executor = ThreadPoolExecutor(max_workers=2)

        self._schema_conn = self._pool.getconn()
        self._schema = SchemaManager(self._schema_conn, self.run_id)
        self._schema.setup(record_classes)

    @contextmanager
    def _connection(self):
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def insert_output(self, output: any):
        if isinstance(output, ModelOutput):
            for record in output.all_records():
                if record.diagnostic:
                    self._executor.submit(self._write_record, record)
                else:
                    self._write_record(record)
        elif isinstance(output, ExternalDataset):
            self._executor.submit(self._write_external_dataset, output)
        else:
            raise ValueError(f"Unsupported output type: {type(output)}")

    def _write_record(self, record):
        record_cls = type(record)
        fields = [f.name for f in dataclasses.fields(record_cls)]
        query = (
            f"INSERT INTO {self.run_id}.{record_cls.table_name} "
            f"({', '.join(fields)}) VALUES ({', '.join(['%s'] * len(fields))})"
        )
        values = [getattr(record, f) for f in fields]
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, values)
            conn.commit()

    def _write_external_dataset(self, dataset):
        query = (
            f"INSERT INTO {self.run_id}.{dataset.table_name} "
            f"({', '.join(dataset.data.keys())}) VALUES ({', '.join(['%s'] * len(dataset.data))})"
        )
        with self._connection() as conn:
            for row in zip(*dataset.data.values()):
                with conn.cursor() as cur:
                    cur.execute(query, row)
            conn.commit()

    def reset_tables(self):
        self._schema.reset_tables()

    def delete_run(self, run_id: str):
        self._schema.delete_run(run_id)

    def list_runs(self) -> list[str]:
        return self._schema.list_runs()

    def close_conn(self):
        self._executor.shutdown(wait=True)
        self._pool.putconn(self._schema_conn)
        self._pool.closeall()
