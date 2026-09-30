from __future__ import annotations
from dataclasses import fields as dataclass_fields
from numbers import Real
from base.input.filter import Filter, validate_where
from base.output.record import Record
from base.output.geometry import Geometry
from base.utils import as_list

_VALID_JOIN_MODES = ("inner", "left", "right", "full")


class KeyJoin:
    """Joins two records where a field of one equals a field of the other.

    `left` and `right` are (record, field) pairs, e.g. (EV, "veh_id") and
    (Battery, "veh_id").

    `join_type` is "inner" (default), "left", "right" or "full", as in SQL.

    A left row that matches several right rows appears once per match.
    """

    __slots__ = (
        "left_record",
        "left_field",
        "right_record",
        "right_field",
        "join_type",
    )

    VALID_MODES = _VALID_JOIN_MODES

    def __init__(
        self,
        left: tuple[type[Record], str],
        right: tuple[type[Record], str],
        join_type: str = "inner",
    ):
        self.left_record, self.left_field = left
        self.right_record, self.right_field = right
        self.join_type = join_type
        self._validate()

    def _validate(self):
        context = f"KeyJoin({self.left_record.__name__} → {self.right_record.__name__})"
        if self.left_record is self.right_record:
            raise TypeError(f"{context}: left and right records must be different")
        for record, field in (
            (self.left_record, self.left_field),
            (self.right_record, self.right_field),
        ):
            valid_fields = {field.name for field in dataclass_fields(record)}
            if field not in valid_fields:
                raise AttributeError(
                    f"{context}: {record.__name__} has no field {field!r}. Valid: {sorted(valid_fields)}"
                )
        if self.join_type not in self.VALID_MODES:
            raise ValueError(
                f"{context}: join_type must be one of {self.VALID_MODES}, got {self.join_type!r}"
            )


_VALID_PREDICATES = ("exact", "nearest")


class SpatialJoin:
    """Joins two Geometry records on their `coords`.

    `left` and `right` are the records, e.g. EV and Station.

    `join_type` is "inner" (default), "left", "right" or "full", as in SQL.

    `predicate` is "nearest" (default) or "exact":
    - "nearest" joins each left row to its closest right row, e.g. the nearest
      station to an EV. Supports only "inner" and "left" joins.
    - "exact" joins each left row to every right row it intersects, e.g. the
      zones a point lies in.

    `max_distance` caps "nearest" matches, in metres. Left rows with nothing
    that close get no match. Not allowed with "exact".

    `where` filters the right record before matching, e.g. only stations with
    DC ports, so "nearest" finds the closest one that passes.
    """

    __slots__ = (
        "left_record",
        "right_record",
        "join_type",
        "predicate",
        "max_distance",
        "where",
    )

    VALID_MODES = _VALID_JOIN_MODES
    VALID_PREDICATES = _VALID_PREDICATES

    def __init__(
        self,
        left: type[Record],
        right: type[Record],
        join_type: str = "inner",
        predicate: str = "nearest",
        max_distance: float | None = None,
        where: Filter | list[Filter] = None,
    ):
        self.left_record = left
        self.right_record = right
        self.join_type = join_type
        self.predicate = predicate
        self.max_distance = max_distance
        self.where = as_list(where) if where is not None else []
        context = (
            f"SpatialJoin({self.left_record.__name__} → {self.right_record.__name__})"
        )
        self._validate_records(context)
        validate_where(self.where, [self.right_record], context)
        self._validate_parameters(context)

    def _validate_records(self, context: str):
        if self.left_record is self.right_record:
            raise TypeError(f"{context}: left and right records must be different")
        for record in (self.left_record, self.right_record):
            if not issubclass(record, Geometry):
                raise TypeError(
                    f"{context}: SpatialJoin requires Geometry records, got {record.__name__} which is not a Geometry record"
                )

    def _validate_parameters(self, context: str):
        if self.join_type not in self.VALID_MODES:
            raise ValueError(
                f"{context}: join_type must be one of {self.VALID_MODES}, got {self.join_type!r}"
            )
        if self.predicate not in self.VALID_PREDICATES:
            raise ValueError(
                f"{context}: predicate must be one of {self.VALID_PREDICATES}, got {self.predicate!r}"
            )
        if self.predicate == "nearest" and self.join_type in ("right", "full"):
            raise ValueError(
                f"{context}: predicate='nearest' does not support join_type={self.join_type!r} "
                "(Postgres has no RIGHT/FULL JOIN LATERAL); use 'inner' or 'left'"
            )
        if self.max_distance is not None:
            if self.predicate != "nearest":
                raise ValueError(
                    f"{context}: max_distance requires predicate='nearest'"
                )
            if (
                isinstance(self.max_distance, bool)
                or not isinstance(self.max_distance, Real)
                or not 0 < self.max_distance < float("inf")
            ):
                raise ValueError(
                    f"{context}: max_distance must be a positive number of metres, got {self.max_distance!r}"
                )
