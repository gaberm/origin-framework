import dataclasses

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

    def _create_table(self, cls):
        query = self._generate_create_table_query(cls)
        with self.conn.cursor() as cur:
            cur.execute(query)
        self.conn.commit()

    def _generate_create_table_query(self, cls) -> str:
        table = f"{self.run_id}.{cls.table_name}"
        columns = ", ".join(
            f"{f.name} {SQL_TYPE_MAP.get(f.type, 'TEXT')}"
            for f in dataclasses.fields(cls)
        )
        key = cls.primary_key
        pk = f", PRIMARY KEY ({', '.join(key)})" if key else ""
        return f"CREATE TABLE IF NOT EXISTS {table} ({columns}{pk});"
