from __future__ import annotations
from dataclasses import dataclass, fields as dataclass_fields
from typing import Any, ClassVar, Sequence
from base.output.record import Record


@dataclass(frozen=True)
class Comparison:
    op: str
    value: Any


def Equal(value: Any) -> Comparison:
    return Comparison("=", value)


def NotEqual(value: Any) -> Comparison:
    return Comparison("!=", value)


def Less(value: Any) -> Comparison:
    return Comparison("<", value)


def LessEqual(value: Any) -> Comparison:
    return Comparison("<=", value)


def Greater(value: Any) -> Comparison:
    return Comparison(">", value)


def GreaterEqual(value: Any) -> Comparison:
    return Comparison(">=", value)


class Filter:
    __slots__ = ("from_", "field", "cmp")

    def __init__(
        self,
        from_or_field: type | str,
        field_or_cmp: str | Comparison,
        cmp: Comparison | None = None,
    ):
        if isinstance(from_or_field, type):
            self.from_, self.field, self.cmp = from_or_field, field_or_cmp, cmp
        else:
            self.from_, self.field, self.cmp = None, from_or_field, field_or_cmp


class Join:
    __slots__ = ("left_entity", "left_field", "right_entity", "right_field")

    def __init__(self, left: tuple[type, str], right: tuple[type, str]):
        self.left_entity, self.left_field = left
        self.right_entity, self.right_field = right


class Fields:
    """Declarative row projection: the columns to select from one or more entities.

    Field names are derived from (and validated against) the source entity,
    so the projection cannot drift from the entity's actual schema.

        select = Fields("veh_id", "soc")                                        # entity inferred
        select = Fields((EV, "veh_id", "soc"))                                  # explicit single entity
        select = Fields((EV, "veh_id", "soc"), (VehicleBattery, "capacity_kwh"))  # join projection
    """

    __slots__ = ("segments",)

    def __init__(self, *args):
        if not args:
            raise TypeError("Fields(...) requires at least one field name")
        segments: list[tuple[type | None, tuple[str, ...]]] = []
        if isinstance(args[0], tuple) and args[0] and isinstance(args[0][0], type):
            for arg in args:
                if (
                    not isinstance(arg, tuple)
                    or not arg
                    or not isinstance(arg[0], type)
                ):
                    raise TypeError(
                        f"Fields: expected (Entity, field, ...) tuple, got {arg!r}"
                    )
                entity, *names = arg
                if not names:
                    raise TypeError(
                        f"Fields: no field names given for {entity.__name__}"
                    )
                segments.append((entity, tuple(names)))
        else:
            raw = (
                args[0]
                if len(args) == 1 and isinstance(args[0], (list, tuple))
                else args
            )
            names = tuple(raw)
            if not names:
                raise TypeError("Fields(...) requires at least one field name")
            segments.append((None, names))
        self.segments = segments

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
    on: ClassVar[Join] = None
    select: ClassVar[type | Fields]
    key: ClassVar[str]

    entities: ClassVar[list[type]]
    is_join: ClassVar[bool]
    row_fields: ClassVar[tuple[str, ...]]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "from_" not in {k for c in cls.__mro__ for k in vars(c)}:
            return
        cls.key = cls.__dict__.get("key", cls.__name__)
        _resolve_entities(cls)
        valid = {e: _field_names(e) for e in cls.entities}
        _resolve_filters(cls, valid)
        _resolve_row(cls, valid)


def _resolve_entities(cls) -> None:
    primary = list(cls.from_) if isinstance(cls.from_, (list, tuple)) else [cls.from_]
    on = getattr(cls, "on", None)
    if isinstance(on, Join):
        seen = set(primary)
        for e in (on.left_entity, on.right_entity):
            if e not in seen:
                primary.append(e)
                seen.add(e)
    cls.entities = primary
    cls.is_join = len(cls.entities) > 1


def _resolve_filters(cls, valid: dict) -> None:
    cls.where = [_make_filter(cls, f, valid) for f in getattr(cls, "where", [])]
    if cls.on is not None:
        cls.on = _make_join(cls, cls.on, valid)
    elif cls.is_join:
        raise TypeError(
            f"{cls.__name__}: {len(cls.entities)} entities require an `on` join condition"
        )


def _resolve_row(cls, valid: dict) -> None:
    row = getattr(cls, "select", None)
    if row is None:
        raise TypeError(
            f"{cls.__name__}: missing `select` (a Fields(...) selection or a TypedDict)"
        )
    if isinstance(row, Fields):
        owner = f"{cls.__name__}.select"
        all_names: list[str] = []
        for entity, names in row.segments:
            if entity is None:
                if len(cls.entities) != 1:
                    raise TypeError(
                        f"{owner}: Fields without an entity requires exactly one entity on the Input; "
                        f"got {[e.__name__ for e in cls.entities]}"
                    )
                entity = cls.entities[0]
            for name in names:
                _check_field(owner, entity, name, valid)
            all_names.extend(names)
        cls.row_fields = tuple(all_names)
    else:
        cls.row_fields = tuple(getattr(row, "__annotations__", {}).keys())
        if not cls.row_fields:
            raise TypeError(f"{cls.__name__}: `row` {row!r} declares no fields")


def _field_names(entity: type) -> set[str]:
    try:
        return {f.name for f in dataclass_fields(entity)}
    except TypeError:
        raise TypeError(
            f"{getattr(entity, '__name__', entity)!r} is not a dataclass entity"
        )


def _check_field(owner: str, entity: type, field: str, valid: dict) -> None:
    if entity not in valid:
        raise TypeError(
            f"{owner}: references {entity.__name__}, "
            f"not in entity={[e.__name__ for e in valid]}"
        )
    if field not in valid[entity]:
        raise AttributeError(
            f"{owner}: {entity.__name__} has no field {field!r}. "
            f"Valid fields: {sorted(valid[entity])}"
        )


def _make_filter(cls, spec, valid) -> Filter:
    if isinstance(spec, Filter):
        from_, field, cmp = spec.from_, spec.field, spec.cmp
    else:
        from_, field, cmp = _unpack(
            cls, spec, 3, "each filter must be (entity, field, Cmp)"
        )
    if from_ is None:
        if len(cls.entities) != 1:
            raise TypeError(
                f"{cls.__name__}: Filter without from_ requires exactly one entity; "
                f"got {[e.__name__ for e in cls.entities]}"
            )
        from_ = cls.entities[0]
    if not isinstance(cmp, Comparison):
        raise TypeError(
            f"{cls.__name__}: filter on {from_.__name__}.{field} needs a comparison "
            f"like Equal(...)/Less(...); got {cmp!r}"
        )
    _check_field(cls.__name__, from_, field, valid)
    return Filter(from_, field, cmp)


def _make_join(cls, spec, valid) -> Join:
    le, lf, re_, rf = (
        (spec.left_entity, spec.left_field, spec.right_entity, spec.right_field)
        if isinstance(spec, Join)
        else _unpack(cls, spec, 4, "`on` must be (LeftEntity, 'lf', RightEntity, 'rf')")
    )
    _check_field(f"{cls.__name__}.on", le, lf, valid)
    _check_field(f"{cls.__name__}.on", re_, rf, valid)
    return Join((le, lf), (re_, rf))


def _unpack(cls, spec, n, msg):
    if not isinstance(spec, (list, tuple)) or len(spec) != n:
        raise TypeError(f"{cls.__name__}: {msg}; got {spec!r}")
    return tuple(spec)
