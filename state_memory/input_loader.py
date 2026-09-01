from contextlib import contextmanager
from typing import Sequence
from base.input.input import Input, KeyJoin, Latest, SpatialJoin
from base.utils import as_list
from supervisory.scheduling.time_window import TimeWindow


_SQL_JOIN_KEYWORDS = {
    "inner": "JOIN",
    "left": "LEFT JOIN",
    "right": "RIGHT JOIN",
    "full": "FULL JOIN",
}


class InputLoader:
    def __init__(self, pool, run_id: str):
        self._pool = pool
        self.run_id = run_id

    @contextmanager
    def _connection(self):
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def load_inputs(
        self,
        input_specs: Input | Sequence[Input],
        time_range: TimeWindow,
    ) -> dict[str, list[dict]]:
        return {
            spec.name: self._load_input(spec, time_range)
            for spec in as_list(input_specs)
        }

    def _load_input(self, input_spec: Input, time_range: TimeWindow) -> list[dict]:
        query, values = self._build_query(input_spec, time_range)
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, values)
                col_names = [desc[0] for desc in cur.description]
                return [dict(zip(col_names, row)) for row in cur.fetchall()]

    def _build_query(
        self, input_spec: Input, time_range: TimeWindow
    ) -> tuple[str, list]:
        fields = self._select_fields(input_spec)
        from_clause = self._from_clause(input_spec)
        time_field = self._time_field(input_spec)
        filter_clauses, filter_values = self._filter_clauses(
            input_spec.where, input_spec.from_
        )
        common = (fields, from_clause, time_field, filter_clauses, filter_values)

        if isinstance(input_spec.read_policy, Latest):
            return self._complete_latest_query(input_spec, time_range, *common)
        return self._complete_windowed_query(input_spec, time_range, *common)

    def _complete_latest_query(
        self,
        input_spec: Input,
        time_range: TimeWindow,
        fields: list[str],
        from_clause: str,
        time_field: str,
        filter_clauses: list[str],
        filter_values: list,
    ) -> tuple[str, list]:
        if self._deduplication_key(input_spec) is not None:
            raise ValueError(
                f"{input_spec.name}: a join with duplicates='drop' cannot be "
                f"combined with read_policy=Latest"
            )
        by = input_spec.read_policy.by
        where = " AND ".join([f"{time_field} < %s"] + filter_clauses)
        values = [time_range.end] + filter_values
        select = fields if by in fields else [by, *fields]
        query = (
            f"SELECT DISTINCT ON ({by}) {', '.join(select)} "
            f"FROM {from_clause} WHERE {where} "
            f"ORDER BY {by}, {time_field} DESC"
        )
        return query, values

    def _complete_windowed_query(
        self,
        input_spec: Input,
        time_range: TimeWindow,
        fields: list[str],
        from_clause: str,
        time_field: str,
        filter_clauses: list[str],
        filter_values: list,
    ) -> tuple[str, list]:
        dedup_key = self._deduplication_key(input_spec)
        where = " AND ".join(
            [f"{time_field} >= %s AND {time_field} < %s"] + filter_clauses
        )
        values = [time_range.start, time_range.end] + filter_values
        distinct = f"DISTINCT ON ({dedup_key}) " if dedup_key else ""
        order_by = f" ORDER BY {dedup_key}" if dedup_key else ""
        query = (
            f"SELECT {distinct}{', '.join(fields)} "
            f"FROM {from_clause} WHERE {where}{order_by}"
        )
        return query, values

    def _select_fields(self, input_spec: Input) -> list[str]:
        return [
            f"{table_name}.{field_name}"
            for table_name, field_names in input_spec.select.selected_fields.items()
            for field_name in field_names
        ]

    def _from_clause(self, input_spec: Input) -> str:
        primary_name = input_spec.from_.table_name
        if not input_spec.on:
            return f"{self.run_id}.{primary_name}"
        join_clauses = (
            self._spatial_join_clause(join)
            if isinstance(join, SpatialJoin)
            else self._join_clause(join)
            for join in input_spec.on
        )
        return f"{self.run_id}.{primary_name} {primary_name} {' '.join(join_clauses)}"

    def _join_clause(self, join: KeyJoin) -> str:
        keyword = _SQL_JOIN_KEYWORDS[join.join_type]
        return (
            f"{keyword} {self.run_id}.{join.right_record.table_name} {join.right_record.table_name} "
            f"ON {join.left_record.table_name}.{join.left_field} = {join.right_record.table_name}.{join.right_field}"
        )

    def _spatial_join_clause(self, join: SpatialJoin) -> str:
        keyword = _SQL_JOIN_KEYWORDS[join.join_type]
        right_table = join.right_record.table_name
        if join.predicate == "nearest":
            return (
                f"{keyword} LATERAL ("
                f"SELECT * FROM {self.run_id}.{right_table} {right_table} "
                f"ORDER BY {join.left_record.table_name}.{join.left_field} <-> "
                f"{right_table}.{join.right_field} LIMIT 1"
                f") {right_table} ON true"
            )
        return (
            f"{keyword} {self.run_id}.{right_table} {right_table} "
            f"ON ST_Intersects({join.left_record.table_name}.{join.left_field}, "
            f"{right_table}.{join.right_field})"
        )

    def _deduplication_key(self, input_spec: Input) -> str | None:
        has_drop_join = any(join.duplicates == "drop" for join in input_spec.on)
        if not has_drop_join:
            return None
        base_table = input_spec.from_
        return ", ".join(f"{base_table.table_name}.{key}" for key in base_table.primary_key)

    def _time_field(self, input_spec: Input) -> str:
        base_table = input_spec.from_
        return f"{base_table.table_name}.{base_table.time_field}"

    def _filter_clauses(self, filters: list, default_record: type) -> tuple[list[str], list]:
        clauses, values = [], []
        for filter_ in filters:
            record = filter_.from_ if filter_.from_ is not None else default_record
            field = f"{record.table_name}.{filter_.field}"
            clauses.append(f"{field} {filter_.condition.operator} %s")
            values.append(filter_.condition.value)
        return clauses, values
