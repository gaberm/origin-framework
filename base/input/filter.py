from __future__ import annotations
from dataclasses import dataclass, fields as dataclass_fields
from typing import Any, Iterable
from base.output.record import Record


@dataclass(frozen=True)
class Condition:
    operator: str
    value: Any


def Equal(value: Any) -> Condition:
    return Condition("=", value)


def NotEqual(value: Any) -> Condition:
    return Condition("!=", value)


def Less(value: Any) -> Condition:
    return Condition("<", value)


def LessEqual(value: Any) -> Condition:
    return Condition("<=", value)


def Greater(value: Any) -> Condition:
    return Condition(">", value)


def GreaterEqual(value: Any) -> Condition:
    return Condition(">=", value)


def In(values: Iterable) -> Condition:
    return Condition("IN", _non_empty_tuple(values, "In"))


def NotIn(values: Iterable) -> Condition:
    return Condition("NOT IN", _non_empty_tuple(values, "NotIn"))


def _non_empty_tuple(values: Iterable, name: str) -> tuple:
    """In and NotIn require a non-empty tuple."""
    values = tuple(values)
    if not values:
        raise ValueError(f"{name} needs at least one value")
    return values


class Filter:
    """Keeps the rows whose field meets a condition. Can be used in two ways:

    * Explicit: Filter(EV, "veh_id", Equal(1)) filters on EV.veh_id.
    * Implicit: Filter("veh_id", Equal(1)) filters on the default record:
    Input.from_ in an Input's where, or the right record in a SpatialJoin's.

    The field (here: "veh_id") must exist on that record (here: EV).
    """

    __slots__ = ("from_", "field", "condition")

    def __init__(
        self,
        from_or_field: type[Record] | str,
        field_or_condition: str | Condition,
        condition: Condition | None = None,
    ):
        if isinstance(from_or_field, type):
            self.from_, self.field, self.condition = (
                from_or_field,
                field_or_condition,
                condition,
            )
        else:
            self.from_, self.field, self.condition = (
                None,
                from_or_field,
                field_or_condition,
            )
        self._validate_condition()

    def _validate_condition(self):
        if not isinstance(self.condition, Condition):
            raise TypeError(
                f"{self.__class__.__name__}.condition must be a Condition; got {type(self.condition).__name__!r}"
            )


def validate_where(where: list, records: list[type[Record]], context: str):
    for filter_ in where:
        if not isinstance(filter_, Filter):
            raise TypeError(
                f"{context}.where: must be (list of) Filter; got {type(filter_).__name__!r}"
            )
        if filter_.from_ is not None and filter_.from_ not in records:
            names = ", ".join(record.__name__ for record in records)
            raise ValueError(
                f"{context}.where: filters apply to {names}, got {filter_.from_.__name__}"
            )
        _validate_fields(records, filter_, context)


def _validate_fields(records: list[type[Record]], filter_: Filter, context: str):
    """A filter without `from_` is validated against the first of `records`, i.e.
    Input.from_ or a SpatialJoin's right record."""
    target = filter_.from_ if filter_.from_ is not None else records[0]
    valid_fields = {field.name for field in dataclass_fields(target)}
    if filter_.field not in valid_fields:
        raise AttributeError(
            f"{context}.where: {target.__name__} has no field {filter_.field!r}. Valid: {sorted(valid_fields)}"
        )
