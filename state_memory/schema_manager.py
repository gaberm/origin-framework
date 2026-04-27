import dataclasses
from records.model_output import ModelOutput
from adapters.data_adapter import ExternalDataset

SQL_TYPE_MAP = {
    int: "INTEGER",
    float: "FLOAT",
    str: "TEXT",
    bool: "BOOLEAN",
}


class SchemaManager:
    def __init__(self, conn, run_id: str):
        self.conn = conn
        self.run_id = run_id

    def setup(self, record_classes: list):
        if self._schema_exists():
            raise RuntimeError(
                f"A simulation run named '{self.run_id}' already exists. "
                f"Choose a different run_id or call delete_run('{self.run_id}') "
                f"to remove it first."
            )
        self._create_schema()
        for cls in record_classes:
            self._create_table(cls)

    def _schema_exists(self) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.schemata
                    WHERE schema_name = %s
                )
                """,
                (self.run_id,),
            )
            return cur.fetchone()[0]

    def _create_schema(self):
        with self.conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {self.run_id}")
        self.conn.commit()

    def create_table(self, cls) -> str:
        if isinstance(cls, ModelOutput):
            query = self._query_for_model_output(cls)
        elif isinstance(cls, ExternalDataset):
            query = self._query_for_external_dataset(cls)
        else:
            raise ValueError(f"Unsupported record class type: {type(cls)}")
        with self.conn.cursor() as cur:
            cur.execute(query)
        self.conn.commit()

    def _query_for_model_output(self, cls) -> str:
        table = f"{self.run_id}.{cls.table_name}"
        columns = ", ".join(
            f"{f.name} {SQL_TYPE_MAP.get(f.type, 'TEXT')}"
            for f in dataclasses.fields(cls)
        )
        pk = f", PRIMARY KEY ({', '.join(cls.primary_key)})" if cls.primary_key else ""
        return f"CREATE TABLE IF NOT EXISTS {table} ({columns}{pk});"

    def _query_for_external_dataset(self, cls) -> str:
        table = f"{self.run_id}.{cls.table_name}"
        columns = ", ".join(
            f"{key} {SQL_TYPE_MAP.get(type(val[0]), 'TEXT')}"
            for key, val in cls.data.items()
        )
        pk = f", PRIMARY KEY ({', '.join(cls.primary_key)})" if cls.primary_key else ""
        return f"CREATE TABLE IF NOT EXISTS {table} ({columns}{pk});"
