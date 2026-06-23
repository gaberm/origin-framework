from __future__ import annotations
from dataclasses import dataclass, fields as dataclass_fields
from typing import Any, ClassVar, Sequence
from base.output.record import Record
from base.input.reconstruction import Windowed, Hold


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
                f"{self.__class__.__name__}: condition must be a Condition; got {type(self.condition).__name__!r}"
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
            valid_fields = {f.name for f in dataclass_fields(record)}
            if field not in valid_fields:
                raise AttributeError(
                    f"{context}: {record.__name__} has no field {field!r}. Valid: {sorted(valid_fields)}"
                )


class Fields:
    __slots__ = ("segments",)

    def __init__(self, *args):
        if not args:
            raise TypeError("Fields(...) requires at least one field name")
        if self._is_explicit(args):
            self.segments = [self._parse_segment(arg) for arg in args]
        else:
            field_names = (
                args[0]
                if len(args) == 1 and isinstance(args[0], (list, tuple))
                else args
            )
            if not field_names:
                raise TypeError("Fields(...) requires at least one field name")
            self._validate_fields(field_names)
            self.segments = [(None, tuple(field_names))]

    @staticmethod
    def _is_explicit(args) -> bool:
        return (
            isinstance(args[0], tuple)
            and bool(args[0])
            and isinstance(args[0][0], type)
        )

    def _parse_segment(self, arg) -> tuple:
        if not (isinstance(arg, tuple) and arg and isinstance(arg[0], type)):
            raise TypeError(f"Fields: expected (Entity, field, ...) tuple, got {arg!r}")
        entity, *field_names = arg
        if not field_names:
            raise TypeError(f"Fields: no field names given for {entity.__name__}")
        valid_fields = {
            dataclass_field.name for dataclass_field in dataclass_fields(entity)
        }
        self._validate_fields(field_names, valid_fields)
        return (entity, tuple(field_names))

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
        for entity, names in self.segments:
            if entity is not None:
                parts.append(getattr(entity, "__name__", repr(entity)))
            parts.append(repr(names))
        return f"Fields({', '.join(parts)})"


class Input:
    from_: ClassVar[type[Record] | list[type[Record]]]
    where: ClassVar[list[Filter]] = []
    on: ClassVar[Join | list[Join]] = []
    select: ClassVar[type | Fields]
    name: ClassVar[str] = None
    read_policy: ClassVar = Windowed()

    def __init_subclass__(cls):
        cls._resolve_name()
        cls._validate_from()
        cls._validate_where()
        cls._validate_on()
        cls._validate_policy()

    def _resolve_name(cls):
        if cls.name is None:
            cls.name = cls.__name__

    def _validate_from(cls):
        if cls.from_ is None:
            raise TypeError(f"{cls.__name__}: from_ is required")
        records = _as_list(cls.from_)
        for record in records:
            if not (isinstance(record, type) and issubclass(record, Record)):
                raise TypeError(
                    f"{cls.__name__}: from_ must be a Record subclass; got {type(record).__name__!r}"
                )

    def _validate_on(cls):
        joins = _as_list(cls.on)
        for join in joins:
            if not isinstance(join, Join):
                raise TypeError(
                    f"{cls.__name__}.on: expected Join, got {type(join).__name__!r}"
                )

    def _validate_where(cls):
        for filter_ in _as_list(cls.where):
            if not isinstance(filter_, Filter):
                raise TypeError(
                    f"{cls.__name__}.where: expected Filter, got {type(filter_).__name__!r}"
                )
            record = filter_.from_ if filter_.from_ is not None else cls.from_
            valid_fields = {field.name for field in dataclass_fields(record)}
            if filter_.field not in valid_fields:
                raise AttributeError(
                    f"{cls.__name__}.where: {record.__name__} has no field {filter_.field!r}. Valid: {sorted(valid_fields)}"
                )

    def _validate_policy(cls):
        if not isinstance(cls.read_policy, (Windowed, Hold)):
            raise TypeError(
                f"{cls.__name__}: read_policy must be Windowed or Hold; got {type(cls.read_policy).__name__!r}"
            )


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]
