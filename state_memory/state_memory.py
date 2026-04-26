import dataclasses
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import psycopg2 as psycopg
from state_memory.validation import validate_run_id
from .schema_manager import SchemaManager
from records.model_output import ModelOutput


def _generate_run_id() -> str:
    return datetime.now().strftime("run_%Y%m%d_%H%M%S")


class StateMemory:
    @classmethod
    def from_config(cls, config) -> "StateMemory":
        from adapters import BaseAdapter

        record_classes = [
            BaseAdapter._registry[config.models[name].adapter].OutputType
            for name in config.models
        ]
        return cls(record_classes=record_classes, **config.db)

    def __init__(self, db_url: str, record_classes: list, run_id: str = None):
        self.run_id = run_id or _generate_run_id()
        validate_run_id(self.run_id)

        self.conn = psycopg.connect(db_url)
        self._schema = SchemaManager(self.conn, self.run_id)
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._schema.setup(record_classes)

    def insert_output(self, output: ModelOutput):
        for record in output.all_records():
            if record.diagnostic:
                self._executor.submit(self._write_record, record)
            else:
                self._write_record(record)

    def _write_record(self, record):
        record_cls = type(record)
        query = self._create_insert_query(record_cls)
        values = [getattr(record, f.name) for f in dataclasses.fields(record_cls)]
        with self.conn.cursor() as cur:
            cur.execute(query, values)
        self.conn.commit()

    def _create_insert_query(self, record) -> str:
        table_name = record.table_name
        fields = [f.name for f in dataclasses.fields(record)]
        return (
            f"INSERT INTO {self.run_id}.{table_name} "
            f"({', '.join(fields)}) VALUES ({', '.join(['%s'] * len(fields))})"
        )

    def reset_tables(self):
        self._schema.reset_tables()

    def delete_run(self, run_id: str):
        self._schema.delete_run(run_id)

    def list_runs(self) -> list[str]:
        return self._schema.list_runs()

    def close_conn(self):
        self._executor.shutdown(wait=True)
        self.conn.close()
