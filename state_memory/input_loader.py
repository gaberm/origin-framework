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
        from_clause, from_values = self._from_clause(input_spec)
        time_field = self._time_field(input_spec)
        filter_clauses, filter_values = self._filter_clauses(
            input_spec.where, input_spec.from_
        )
        common = (fields, from_clause, time_field, filter_clauses, filter_values)

        if isinstance(input_spec.read_policy, Latest):
            query, values = self._complete_latest_query(input_spec, time_range, *common)
        else:
            query, values = self._complete_windowed_query(
                input_spec, time_range, *common
            )
        # The FROM clause's placeholders come before the WHERE clause's.
        return query, from_values + values

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
        order_by = ", ".join([by, f"{time_field} DESC", *self._row_key(input_spec)])
        query = (
            f"SELECT DISTINCT ON ({by}) {', '.join(select)} "
            f"FROM {from_clause} WHERE {where} "
            f"ORDER BY {order_by}"
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
        # A fixed row order, so results don't depend on how Postgres happens
        # to return rows with equal times. DISTINCT ON has to sort by its key
        # first; the joined keys then decide which duplicate is kept.
        joined_keys = self._joined_keys(input_spec)
        if dedup_key:
            distinct = f"DISTINCT ON ({dedup_key}) "
            order_by = [dedup_key, *joined_keys]
        else:
            distinct = ""
            order_by = [time_field, *self._row_key(input_spec), *joined_keys]
        query = (
            f"SELECT {distinct}{', '.join(fields)} "
            f"FROM {from_clause} WHERE {where} ORDER BY {', '.join(order_by)}"
        )
        return query, values

    def _select_fields(self, input_spec: Input) -> list[str]:
        return [
            f"{table_name}.{field_name}"
            for table_name, field_names in input_spec.select.selected_fields.items()
            for field_name in field_names
        ]

    def _from_clause(self, input_spec: Input) -> tuple[str, list]:
        primary_name = input_spec.from_.table_name
        if not input_spec.on:
            return f"{self.run_id}.{primary_name}", []
        join_clauses, values = [], []
        for join in input_spec.on:
            if isinstance(join, SpatialJoin):
                clause, join_values = self._spatial_join_clause(join)
                values += join_values
            else:
                clause = self._join_clause(join)
            join_clauses.append(clause)
        return (
            f"{self.run_id}.{primary_name} {primary_name} {' '.join(join_clauses)}",
            values,
        )

    def _join_clause(self, join: KeyJoin) -> str:
        keyword = _SQL_JOIN_KEYWORDS[join.join_type]
        return (
            f"{keyword} {self.run_id}.{join.right_record.table_name} {join.right_record.table_name} "
            f"ON {join.left_record.table_name}.{join.left_field} = {join.right_record.table_name}.{join.right_field}"
        )

    def _spatial_join_clause(self, join: SpatialJoin) -> tuple[str, list]:
        keyword = _SQL_JOIN_KEYWORDS[join.join_type]
        right_table = join.right_record.table_name
        left = f"{join.left_record.table_name}.{join.left_field}"
        right = f"{right_table}.{join.right_field}"
        # The join's own filters narrow the candidates before matching.
        filter_clauses, values = self._filter_clauses(join.where, join.right_record)
        if join.predicate == "nearest":
            candidates = self._nearest_candidates(
                left, right, join.max_distance, filter_clauses
            )
            return (
                f"{keyword} LATERAL ("
                f"SELECT * FROM {self.run_id}.{right_table} {right_table} "
                f"{candidates} LIMIT 1"
                f") {right_table} ON true"
            ), values
        conditions = " AND ".join([f"ST_Intersects({left}, {right})"] + filter_clauses)
        return (
            f"{keyword} {self.run_id}.{right_table} {right_table} ON {conditions}"
        ), values

    @staticmethod
    def _nearest_candidates(
        left: str, right: str, max_distance: float | None, filter_clauses: list[str]
    ) -> str:
        conditions = list(filter_clauses)
        if max_distance is None:
            order = f"{left} <-> {right}"
        else:
            # Distances in metres, whatever SRID the columns are stored in.
            left_geog = f"ST_Transform({left}, 4326)::geography"
            right_geog = f"ST_Transform({right}, 4326)::geography"
            conditions.insert(
                0, f"ST_DWithin({left_geog}, {right_geog}, {float(max_distance)})"
            )
            order = f"ST_Distance({left_geog}, {right_geog})"
        where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
        return f"{where}ORDER BY {order}"

    def _deduplication_key(self, input_spec: Input) -> str | None:
        has_drop_join = any(join.duplicates == "drop" for join in input_spec.on)
        if not has_drop_join:
            return None
        base_table = input_spec.from_
        return ", ".join(
            f"{base_table.table_name}.{key}" for key in base_table.primary_key
        )

    @staticmethod
    def _row_key(input_spec: Input) -> list[str]:
        record = input_spec.from_
        return [f"{record.table_name}.{key}" for key in record.primary_key]

    @staticmethod
    def _joined_keys(input_spec: Input) -> list[str]:
        keys = [
            f"{join.right_record.table_name}.{key}"
            for join in input_spec.on
            for key in join.right_record.primary_key
        ]
        return list(dict.fromkeys(keys))

    def _time_field(self, input_spec: Input) -> str:
        base_table = input_spec.from_
        return f"{base_table.table_name}.{base_table.time_field}"

    def _filter_clauses(
        self, filters: list, default_record: type
    ) -> tuple[list[str], list]:
        clauses, values = [], []
        for filter_ in filters:
            record = filter_.from_ if filter_.from_ is not None else default_record
            field = f"{record.table_name}.{filter_.field}"
            clauses.append(f"{field} {filter_.condition.operator} %s")
            values.append(filter_.condition.value)
        return clauses, values
