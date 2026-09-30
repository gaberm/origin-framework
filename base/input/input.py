from __future__ import annotations
from dataclasses import dataclass, fields as dataclass_fields
from base.input.fields import Fields
from base.input.filter import Filter, validate_where
from base.input.join import KeyJoin, SpatialJoin
from base.output.record import Record
from base.utils import as_list


class ReadPolicy:
    kind: str


@dataclass(frozen=True)
class Window(ReadPolicy):
    """Return all rows whose timestamp lies in [start, end)."""

    kind: str = "window"


@dataclass(frozen=True)
class Latest(ReadPolicy):
    """Only return the latest value per `by` key with timestamp in [start, end)."""

    by: str
    kind: str = "latest"


class Input:
    """Describes, like an SQL query, what the StateMemory reads for a model.

    `name` is the key the rows are returned under, e.g. {"EV": [{"veh_id": 1, "soc": 0.5}, ...]}.

    `from_` is the record to read.

    `select` names the fields to return (see Fields).

    `where` keeps only the rows that match all its filters (see Filter).

    `on` joins other records, in the order given. Each join starts from
    `from_` or a record joined before it.

    `read_policy` says which rows are read: Window() (the default) or Latest.

    `name`, `from_` and `select` are required, e.g.
    Input(name="EV", from_=EV, select=Fields("veh_id", "soc")).
    """

    def __init__(
        self,
        *,
        name: str,
        from_: type[Record],
        select: Fields,
        where: Filter | list[Filter] = None,
        on: KeyJoin | SpatialJoin | list[KeyJoin | SpatialJoin] = None,
        read_policy: ReadPolicy = Window(),
    ):
        self.name = name
        self.from_ = from_
        self.where = as_list(where) if where is not None else []
        self.on = as_list(on) if on is not None else []
        self.select = select
        self.read_policy = read_policy
        self._validate_from()
        self._validate_on()
        validate_where(self.where, self.records(), self.name)
        self._resolve_select()
        self._validate_policy()

    def _validate_from(self):
        if not (isinstance(self.from_, type) and issubclass(self.from_, Record)):
            raise TypeError(
                f"{self.name}: from_ must be a Record subclass; got {type(self.from_).__name__!r}"
            )

    def _validate_on(self):
        """Each join must start from `from_` or a record joined before it, and
        join a record not yet in the query."""
        joined = [self.from_]
        for join in self.on:
            if not isinstance(join, (KeyJoin, SpatialJoin)):
                raise TypeError(
                    f"{self.name}.on: must be (list of) KeyJoin/SpatialJoin; got {type(join).__name__!r}"
                )
            names = ", ".join(record.__name__ for record in joined)
            if join.left_record not in joined:
                raise ValueError(
                    f"{self.name}.on: joins must start from {names}, got {join.left_record.__name__}"
                )
            if join.right_record in joined:
                raise ValueError(
                    f"{self.name}.on: {join.right_record.__name__} is already in the query"
                )
            joined.append(join.right_record)

    def records(self) -> list[type[Record]]:
        """The records this input reads: `from_` first, then those it joins."""
        return [self.from_] + [join.right_record for join in self.on]

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
                    raise AttributeError(
                        f"{self.name}.select: {self.from_.__name__} has no field {field_name!r}. Valid: {sorted(valid_fields)}"
                    )
            self.selected_fields[self.from_.table_name] = field_names

    def _validate_selected_tables(self):
        """Selected fields must come from `from_` or a joined record."""
        table_names = [record.table_name for record in self.records()]
        for table_name in self.selected_fields:
            if table_name not in table_names:
                raise ValueError(
                    f"{self.name}.select: fields must come from {', '.join(table_names)}, got {table_name}"
                )

    def _validate_policy(self):
        if not isinstance(self.read_policy, (Window, Latest)):
            raise TypeError(
                f"{self.name}.read_policy must be Window() or Latest(...); got {type(self.read_policy).__name__!r}"
            )
