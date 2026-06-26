import dataclasses
from typing import Union, get_args, get_origin, get_type_hints
from base import Record

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

    def setup(self, entity_classes: list):
        if self._schema_exists():
            raise RuntimeError(
                f"A simulation run named '{self.run_id}' already exists. "
                f"Choose a different run_id or call delete_run('{self.run_id}') "
                f"to remove it first."
            )
        self._create_schema()
        for cls in entity_classes:
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

    def _create_table(self, cls: type[Record]) -> None:
        with self.conn.cursor() as cur:
            cur.execute(self._get_table_query(cls))
            for col in cls.indexed:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{cls.table_name}_{col} "
                    f"ON {self.run_id}.{cls.table_name} ({col});"
                )
        self.conn.commit()

    def _get_table_query(self, cls: type[Record]) -> str:
        hints = get_type_hints(cls)
        table = f"{self.run_id}.{cls.table_name}"
        columns = ", ".join(
            f"{f.name} {self._get_sql_type(hints[f.name])}"
            for f in dataclasses.fields(cls)
        )
        pk = f", PRIMARY KEY ({', '.join(cls.primary_key)})" if cls.primary_key else ""
        return f"CREATE TABLE IF NOT EXISTS {table} ({columns}{pk});"

    def reset_tables(self, drop_tables: bool = False):
        with self.conn.cursor() as cur:
            if drop_tables:
                cur.execute(f"DROP SCHEMA IF EXISTS {self.run_id} CASCADE")
                self.conn.commit()
                self._create_schema()
            else:
                cur.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = %s",
                    (self.run_id,),
                )
                tables = [row[0] for row in cur.fetchall()]
                for table in tables:
                    cur.execute(f"TRUNCATE TABLE {self.run_id}.{table}")
        self.conn.commit()

    def delete_run(self, run_id: str):
        with self.conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {run_id} CASCADE")
        self.conn.commit()

    def setup_external_table(self, dataset) -> None:
        columns = []
        for col, values in dataset.data.items():
            sample = next((v for v in values if v is not None), None)
            if isinstance(sample, bool):
                sql_type = "BOOLEAN"
            elif isinstance(sample, int):
                sql_type = "INTEGER"
            elif isinstance(sample, float):
                sql_type = "FLOAT"
            else:
                sql_type = "TEXT"
            columns.append(f"{col} {sql_type}")
        pk = (
            f", PRIMARY KEY ({', '.join(dataset.primary_key)})"
            if dataset.primary_key
            else ""
        )
        with self.conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {self.run_id}.{dataset.table_name} "
                f"({', '.join(columns)}{pk});"
            )
            for col in dataset.indexed:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{dataset.table_name}_{col} "
                    f"ON {self.run_id}.{dataset.table_name} ({col});"
                )
        self.conn.commit()

    def _get_sql_type(self, hint) -> str:
        if get_origin(hint) is Union:
            non_none = [a for a in get_args(hint) if a is not type(None)]
            # TODO: multi-type unions (e.g. str | int | None) silently resolve to the
            # first type — this may produce the wrong SQL type if order changes.
            hint = non_none[0] if non_none else str
        return SQL_TYPE_MAP.get(hint, "TEXT")
