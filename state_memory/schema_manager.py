import dataclasses
import types
from typing import Union, get_args, get_origin, get_type_hints
from base import Record
from base.output.entity import refs
from base.output.geometry import Geometry

SQL_TYPE_MAP = {
    int: "INTEGER",
    float: "FLOAT",
    str: "TEXT",
    bool: "BOOLEAN",
}

# Columns the state memory adds to Geometry tables (see location.py).
GEOMETRY_COLUMNS = {"coords": "geometry(Geometry, 4326)", "h3_ids": "TEXT[]"}
# Geometry fields with no column: coords are stored in 4326, which `coords`
# itself records.
DROPPED_GEOMETRY_FIELDS = {"epsg_code"}


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
        # After all tables exist, so a reference can come before its target.
        for cls in entity_classes:
            self._add_foreign_keys(cls, entity_classes)

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
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            cur.execute(f"CREATE SCHEMA {self.run_id}")
        self.conn.commit()

    def _create_table(self, cls: type[Record]) -> None:
        hints = get_type_hints(cls)
        with self.conn.cursor() as cur:
            cur.execute(self._get_table_query(cls))
            for col in self._indexed_columns(cls):
                is_array = self._get_sql_type(hints[col]).endswith("[]")
                method = "GIN" if is_array else "BTREE"
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{cls.table_name}_{col} "
                    f"ON {self.run_id}.{cls.table_name} USING {method} ({col});"
                )
            if issubclass(cls, Geometry):
                table = f"{self.run_id}.{cls.table_name}"
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{cls.table_name}_coords_gist "
                    f"ON {table} USING GIST (coords);"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{cls.table_name}_h3_ids "
                    f"ON {table} USING GIN (h3_ids);"
                )
        self.conn.commit()

    def _add_foreign_keys(self, cls: type[Record], entity_classes: list) -> None:
        """A foreign key per ref() field whose target has a table in the run."""
        with self.conn.cursor() as cur:
            for field_name, target in refs(cls).items():
                if target not in entity_classes:
                    continue
                cur.execute(
                    f"ALTER TABLE {self.run_id}.{cls.table_name} "
                    f"ADD CONSTRAINT fk_{cls.table_name}_{field_name} "
                    f"FOREIGN KEY ({field_name}) "
                    f"REFERENCES {self.run_id}.{target.table_name} "
                    f"({target.id_field[0]});"
                )
        self.conn.commit()

    @staticmethod
    def _indexed_columns(cls: type[Record]) -> list[str]:
        covered = cls.primary_key[:1]
        return [col for col in cls.indexed if col not in covered]

    def _get_table_query(self, cls: type[Record]) -> str:
        hints = get_type_hints(cls)
        table = f"{self.run_id}.{cls.table_name}"
        columns = {
            f.name: self._get_sql_type(hints[f.name]) for f in dataclasses.fields(cls)
        }
        if issubclass(cls, Geometry):
            for name in DROPPED_GEOMETRY_FIELDS:
                del columns[name]
            columns.update(GEOMETRY_COLUMNS)
        columns = ", ".join(f"{name} {sql_type}" for name, sql_type in columns.items())
        pk = ", ".join(cls.primary_key)
        return f"CREATE TABLE IF NOT EXISTS {table} ({columns}, PRIMARY KEY ({pk}));"

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
                tables = [f"{self.run_id}.{row[0]}" for row in cur.fetchall()]
                # In one statement: Postgres refuses to truncate a table that
                # another table's foreign key references on its own.
                if tables:
                    cur.execute(f"TRUNCATE TABLE {', '.join(tables)}")
        self.conn.commit()

    def delete_run(self, run_id: str):
        with self.conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {run_id} CASCADE")
        self.conn.commit()

    def _get_sql_type(self, hint) -> str:
        if get_origin(hint) in (Union, types.UnionType):
            non_none = [a for a in get_args(hint) if a is not type(None)]
            hint = non_none[0] if non_none else str
        if get_origin(hint) is list:
            (item,) = get_args(hint)
            return f"{self._get_sql_type(item)}[]"
        return SQL_TYPE_MAP.get(hint, "TEXT")
