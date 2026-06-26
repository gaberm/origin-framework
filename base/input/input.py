from __future__ import annotations
from dataclasses import dataclass, fields as dataclass_fields
from typing import Any
from base.output.record import Record
from base.utils import as_list


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


class Filter:
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


class Join:
    __slots__ = ("left_record", "left_field", "right_record", "right_field")

    def __init__(self, left: tuple[type[Record], str], right: tuple[type[Record], str]):
        self.left_record, self.left_field = left
        self.right_record, self.right_field = right
        self._validate()

    def _validate(self):
        context = f"Join({self.left_record.__name__} → {self.right_record.__name__})"
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


class Fields:
    __slots__ = ("selected_fields",)

    def __init__(self, *args):
        if not args:
            raise TypeError("Fields(...) requires at least one field name")
        self.selected_fields = {}
        if self._is_explicit(args):
            for arg in args:
                self.selected_fields.update(self._parse_field(arg))
        else:
            field_names = (
                args[0]
                if len(args) == 1 and isinstance(args[0], (list, tuple))
                else args
            )
            if not field_names:
                raise TypeError("Fields(...) requires at least one field name")
            self._validate_fields(field_names)
            self.selected_fields = {None: tuple(field_names)}

    @staticmethod
    def _is_explicit(args) -> bool:
        return (
            isinstance(args[0], tuple)
            and bool(args[0])
            and isinstance(args[0][0], type)
        )

    def _parse_field(self, arg) -> tuple:
        if not (isinstance(arg, tuple) and arg and isinstance(arg[0], type)):
            raise TypeError(f"Fields: expected (Entity, field, ...) tuple, got {arg!r}")
        entity, *field_names = arg
        if not field_names:
            raise TypeError(f"Fields: no field names given for {entity.__name__}")
        valid_fields = {
            dataclass_field.name for dataclass_field in dataclass_fields(entity)
        }
        self._validate_fields(field_names, valid_fields)
        return {entity.table_name: tuple(field_names)}

    @staticmethod
    def _validate_fields(field_names, valid_fields: set[str] = None) -> None:
        for field_name in field_names:
            if not isinstance(field_name, str):
                raise TypeError(
                    f"Fields: expected string field name, got {field_name!r}"
                )
            if valid_fields is not None and field_name not in valid_fields:
                raise AttributeError(
                    f"Fields: no field {field_name!r}. Valid: {sorted(valid_fields)}"
                )

    def __repr__(self) -> str:
        parts = []
        for table_name, names in self.selected_fields.items():
            if table_name is not None:
                parts.append(table_name)
            parts.append(repr(names))
        return f"Fields({', '.join(parts)})"


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
    def __init__(
        self,
        *,
        name: str,
        from_: type[Record] | list[type[Record]],
        select: Fields,
        where: Filter | list[Filter] = None,
        on: Join | list[Join] = None,
        read_policy: ReadPolicy = None,
    ):
        self.name = name
        self.from_ = from_
        self.where = as_list(where) if where is not None else []
        self.on = as_list(on) if on is not None else []
        self.select = select
        self.read_policy = read_policy if read_policy is not None else Window()
        self._validate_from()
        self._validate_where()
        self._validate_on()
        self._resolve_select()
        self._validate_policy()

    def _validate_from(self):
        records = as_list(self.from_)
        for record in records:
            if not (isinstance(record, type) and issubclass(record, Record)):
                raise TypeError(
                    f"{self.name}: from_ must be a Record subclass; got {type(record).__name__!r}"
                )

    def _validate_on(self):
        for join in self.on:
            if not isinstance(join, Join):
                raise TypeError(
                    f"{self.name}.on: must be (list of) Join; got {type(join).__name__!r}"
                )

    def _validate_where(self):
        for filter_ in self.where:
            if not isinstance(filter_, Filter):
                raise TypeError(
                    f"{self.name}.where: must be (list of) Filter; got {type(filter_).__name__!r}"
                )
            record = filter_.from_ if filter_.from_ is not None else self.from_
            valid_fields = {field.name for field in dataclass_fields(record)}
            if filter_.field not in valid_fields:
                raise AttributeError(
                    f"{self.name}.where: {record.__name__} has no field {filter_.field!r}. Valid: {sorted(valid_fields)}"
                )

    def _resolve_select(self):
        if not isinstance(self.select, Fields):
            raise TypeError(
                f"{self.name}.select must be Fields; got {type(self.select).__name__!r}"
            )
        if None in self.select.selected_fields:
            field_names = self.select.selected_fields.pop(None)
            from_record = self.from_ if not isinstance(self.from_, list) else self.from_[0]
            valid_fields = {field.name for field in dataclass_fields(from_record)}
            for field_name in field_names:
                if field_name not in valid_fields:
                    raise AttributeError(
                        f"{self.name}.select: {from_record.__name__} has no field {field_name!r}. Valid: {sorted(valid_fields)}"
                    )
            self.select.selected_fields[from_record.table_name] = field_names

    def _validate_policy(self):
        if not isinstance(self.read_policy, (Window, Latest)):
            raise TypeError(
                f"{self.name}.read_policy must be Window() or Latest(...); got {type(self.read_policy).__name__!r}"
            )
