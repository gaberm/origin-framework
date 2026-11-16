from __future__ import annotations
from dataclasses import dataclass, fields as dataclass_fields
from base.input.fields import Fields
from base.input.filter import Filter, validate_where
from base.input.join import KeyJoin, SpatialJoin
from base.output.entity import Entity
from base.output.record import Record
from base.utils import as_list


class ReadPolicy:
    """Base class for Window and Latest"""


@dataclass(frozen=True)
class Window(ReadPolicy):
    """Return all rows whose timestamp lies in [start, end). For an Entity,
    return those active during it: started before `end` and not ended by
    `start`, where a missing `start_s` or `end_s` counts as no limit.

    For example, veh_1 has rows at 10 s, 70 s and 80 s and veh_2 one at 30 s.
    For the window [60, 120), Window() returns veh_1's rows at 70 s and 80 s.
    """


@dataclass(frozen=True)
class Latest(ReadPolicy):
    """Return only the latest row per `by` key whose timestamp lies in
    [start, end). A key with no row in the window isn't returned.

    For example, EV has these rows:

        EV(veh_id="veh_1", soc=0.50, time_s=1)
        EV(veh_id="veh_1", soc=0.49, time_s=2)
        EV(veh_id="veh_1", soc=0.48, time_s=3)

        EV(veh_id="veh_2", soc=0.50, time_s=0)
        EV(veh_id="veh_2", soc=0.49, time_s=4)

    For the window [1, 3), Latest(by="veh_id") returns
    {"EV": [{"veh_id": "veh_1", "soc": 0.49}]}. The row at 3 s is at the
    window's end, so it's left out, and veh_2 has no row in the window.
    """

    by: str


class Input:
    """Describes, like an SQL query, what the StateMemory reads for a model.

    `name` is the key the rows are returned under, e.g. {"EV": [{"veh_id": 1, "soc": 0.5}, ...]}.

    `from_` is the record to read.

    `select` names the fields to return (see Fields).

    `where` keeps only the rows that match all its filters (see Filter).

    `join` joins other records, in the order given. Each join starts from
    `from_` or a record joined before it.

    `read_policy` says which rows are read: Window() (the default) or Latest.
    An Entity has one row per id, so it can only be read with Window().
    """

    def __init__(
        self,
        *,
        name: str,
        from_: type[Record],
        select: Fields,
        where: Filter | list[Filter] | None = None,
        join: KeyJoin | SpatialJoin | list[KeyJoin | SpatialJoin] | None = None,
        read_policy: ReadPolicy = Window(),
    ):
        self.name = name
        self.from_ = from_
        self.where = as_list(where) if where is not None else []
        self.join = as_list(join) if join is not None else []
        self.select = select
        self.read_policy = read_policy
        self._validate_from()
        self._validate_join()
        validate_where(self.where, self.records(), self.name)
        self._resolve_select()
        self._validate_read_policy()

    def _validate_from(self):
        if not (isinstance(self.from_, type) and issubclass(self.from_, Record)):
            raise TypeError(
                f"{self.name}.from_ must be a Record subclass; got {type(self.from_).__name__!r}"
            )

    def _validate_join(self):
        """Each join must start from `from_` or a record joined before it, and
        join a record not yet in the query."""
        joined = [self.from_]
        for join in self.join:
            if not isinstance(join, (KeyJoin, SpatialJoin)):
                raise TypeError(
                    f"{self.name}.join must be a KeyJoin or SpatialJoin, or a list of them; "
                    f"got {type(join).__name__!r}"
                )
            names = ", ".join(record.__name__ for record in joined)
            if join.left_record not in joined:
                raise ValueError(
                    f"{self.name}.join: joins must start from {names}; got {join.left_record.__name__}"
                )
            if join.right_record in joined:
                raise ValueError(
                    f"{self.name}.join: {join.right_record.__name__} is already in the query"
                )
            joined.append(join.right_record)

    def records(self) -> list[type[Record]]:
        """The records this input reads: `from_` first, then those it joins."""
        return [self.from_] + [join.right_record for join in self.join]

    def _resolve_select(self):
        """Check `select` and set `selected_fields` from it."""
        if not isinstance(self.select, Fields):
            raise TypeError(
                f"{self.name}.select must be Fields; got {type(self.select).__name__!r}"
            )
        self._resolve_implicit_fields()
        self._validate_selected_tables()

    def _resolve_implicit_fields(self):
        """Set `selected_fields` to the field names per table, with implicit
        fields under `from_` once checked against it. `select` itself is left
        as is, so one Fields can be shared between inputs."""
        self.selected_fields = dict(self.select.selected_fields)
        if None in self.selected_fields:
            field_names = self.selected_fields.pop(None)
            valid_fields = {field.name for field in dataclass_fields(self.from_)}
            for field_name in field_names:
                if field_name not in valid_fields:
                    raise ValueError(
                        f"{self.name}.select: {self.from_.__name__} has no field {field_name!r}. Valid: {sorted(valid_fields)}"
                    )
            self.selected_fields[self.from_.table_name] = field_names

    def _validate_selected_tables(self):
        """Selected fields must come from `from_` or a joined record."""
        table_names = [record.table_name for record in self.records()]
        for table_name in self.selected_fields:
            if table_name not in table_names:
                raise ValueError(
                    f"{self.name}.select: fields must come from {', '.join(table_names)}; got {table_name}"
                )

    def _validate_read_policy(self):
        if not isinstance(self.read_policy, (Window, Latest)):
            raise TypeError(
                f"{self.name}.read_policy must be Window() or Latest(...); got {type(self.read_policy).__name__!r}"
            )
        if isinstance(self.read_policy, Latest) and issubclass(self.from_, Entity):
            raise ValueError(
                f"{self.name}.read_policy: {self.from_.__name__} is an Entity, "
                f"which has one row per id; read it with Window()"
            )
