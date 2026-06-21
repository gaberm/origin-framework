from contextlib import contextmanager
from typing import Sequence
from base.input.input import Input, Join
from supervisory.time.time import TimeRange


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
        input_specs: type[Input] | Sequence[type[Input]],
        time_range: TimeRange,
    ) -> dict[str, list[dict]]:
        specs = (
            list(input_specs)
            if isinstance(input_specs, (list, tuple))
            else [input_specs]
        )
        return {spec.key: self._load_one(spec, time_range) for spec in specs}

    def _load_one(self, input_spec: type[Input], time_range: TimeRange) -> list[dict]:
        query, values = self._get_fetch_query(input_spec, time_range)
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, values)
                col_names = [desc[0] for desc in cur.description]
                return [dict(zip(col_names, row)) for row in cur.fetchall()]

    def _get_fetch_query(
        self, input_spec: type[Input], time_range: TimeRange
    ) -> tuple[str, list]:
        fields = list(input_spec.row_fields)
        if input_spec.is_join:
            return self._get_join_query(input_spec, time_range)
        return self._get_simple_query(input_spec, fields, time_range)

    def _get_simple_query(
        self, input_spec: type[Input], fields: list[str], time_range: TimeRange
    ) -> tuple[str, list]:
        entity = input_spec.from_
        table = f"{self.run_id}.{entity.table_name}"
        where, values = self._build_where(
            entity.time_field, time_range, input_spec.where
        )
        return f"SELECT {', '.join(fields)} FROM {table} WHERE {where}", values

    def _get_join_query(
        self, input_spec: type[Input], time_range: TimeRange
    ) -> tuple[str, list]:
        join: Join = input_spec.on
        la, ra = join.left_entity.table_name, join.right_entity.table_name
        where, values = self._build_where(
            f"{la}.{join.left_entity.time_field}",
            time_range,
            input_spec.where,
            aliased=True,
        )
        qualified = [
            f"{entity.table_name}.{field}"
            for entity, names in input_spec.select.segments
            for field in names
        ]
        query = (
            f"SELECT {', '.join(qualified)} "
            f"FROM {self.run_id}.{la} {la} "
            f"JOIN {self.run_id}.{ra} {ra} ON {la}.{join.left_field} = {ra}.{join.right_field} "
            f"WHERE {where}"
        )
        return query, values

    def _build_where(
        self,
        time_field: str,
        time_range: TimeRange,
        filters: list,
        aliased: bool = False,
    ) -> tuple[str, list]:
        clauses = [f"{time_field} >= %s AND {time_field} < %s"]
        values = [time_range.start_time, time_range.end_time]
        for f in filters:
            field = f"{f.from_.table_name}.{f.field}" if aliased else f.field
            clauses.append(f"{field} {f.cmp.op} %s")
            values.append(f.cmp.value)
        return " AND ".join(clauses), values
