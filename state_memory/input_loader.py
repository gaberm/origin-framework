from contextlib import contextmanager
from typing import Sequence
from base.input.input import Input, Latest
from base.utils import as_list
from supervisory.scheduling.time_window import TimeWindow


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

        if isinstance(input_spec.read_policy, Latest):
            by = input_spec.read_policy.by
            where = " AND ".join([f"{time_field} < %s"] + filter_clauses)
            values = [time_range.end] + filter_values
            select = fields if by in fields else [by, *fields]
            query = (
                f"SELECT DISTINCT ON ({by}) {', '.join(select)} "
                f"FROM {from_clause} WHERE {where} "
                f"ORDER BY {by}, {time_field} DESC"
            )
        else:
            where = " AND ".join(
                [f"{time_field} >= %s AND {time_field} < %s"] + filter_clauses
            )
            values = [time_range.start, time_range.end] + filter_values
            query = f"SELECT {', '.join(fields)} FROM {from_clause} WHERE {where}"

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
        join_sql = " ".join(
            f"JOIN {self.run_id}.{j.right_record.table_name} {j.right_record.table_name} "
            f"ON {j.left_record.table_name}.{j.left_field} = {j.right_record.table_name}.{j.right_field}"
            for j in input_spec.on
        )
        return f"{self.run_id}.{primary_name} {primary_name} {join_sql}"

    def _time_field(self, input_spec: Input) -> str:
        primary = input_spec.from_
        return f"{primary.table_name}.{primary.time_field}"

    def _filter_clauses(self, filters: list, default_record: type) -> tuple[list[str], list]:
        clauses, values = [], []
        for filter_ in filters:
            record = filter_.from_ if filter_.from_ is not None else default_record
            field = f"{record.table_name}.{filter_.field}"
            clauses.append(f"{field} {filter_.condition.operator} %s")
            values.append(filter_.condition.value)
        return clauses, values
