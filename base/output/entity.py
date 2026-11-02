import sys
import types
from functools import cache
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints
from dataclasses import dataclass, field, fields
from datetime import timedelta
from numbers import Real
from base.output.identifier import Identifier
from base.output.record import Record


def ref(target: "type[Entity] | str") -> Any:
    """`station_id: str = ref(Station)` holds a Station's id.

    `target` is the referenced Entity class, or its name if it is defined
    later in the same module. It is stored as the field's `metadata["ref"]`.

    The state memory adds a foreign key for it when the target has a table
    in the run, and KeyJoin(record, target) joins on it.
    """
    return field(metadata={"ref": target})


def refs(record: type[Record]) -> "dict[str, type[Entity]]":
    """The fields of `record` declared with ref(), each with the Entity it
    references. A target given by name is looked up in the module that
    defines `record`.

    Raises if a target can't be found or isn't a concrete Entity, if it has
    a composite id or a primary key other than its id, or if the field isn't
    a string; a reference holds one id.
    """
    return dict(_resolve_refs(record))


@cache
def _resolve_refs(record: type[Record]) -> "tuple[tuple[str, type[Entity]], ...]":
    hints = get_type_hints(record)
    resolved = []
    for record_field in fields(record):
        target = record_field.metadata.get("ref")
        if target is None:
            continue
        context = f"{record.__name__}.{record_field.name}"
        if isinstance(target, str):
            target = _resolve_name(record, target, context)
        _validate_target(target, context)
        _validate_ref_type(hints[record_field.name], context)
        resolved.append((record_field.name, target))
    return tuple(resolved)


def _resolve_name(record: type[Record], name: str, context: str) -> type:
    target = vars(sys.modules[record.__module__]).get(name)
    if target is None:
        raise NameError(
            f"{context}: ref({name!r}) names no class in {record.__module__}; "
            f"import it there or pass the class itself"
        )
    return target


def _validate_target(target, context: str):
    if not (
        isinstance(target, type) and issubclass(target, Entity) and target is not Entity
    ):
        raise TypeError(f"{context}: ref() must name an Entity subclass; got {target!r}")
    if len(target.id_field) != 1:
        raise TypeError(
            f"{context}: {target.__name__} has the composite id {target.id_field}; "
            f"ref() holds one id"
        )
    if tuple(target.primary_key) != target.id_field:
        raise TypeError(
            f"{context}: {target.__name__}'s primary key {target.primary_key} "
            f"isn't its id {target.id_field}, so it can't be referenced"
        )


def _validate_ref_type(hint, context: str):
    if get_origin(hint) in (Union, types.UnionType):
        hints = {arg for arg in get_args(hint) if arg is not type(None)}
    else:
        hints = {hint}
    if hints != {str}:
        raise TypeError(f"{context}: a ref() field holds an id, a str; got {hint!r}")


@dataclass(kw_only=True)
class Entity(Record, Identifier, abstract=True):
    """A record identified by its `id_field`, one field or several, which is
    its primary key unless the class sets `primary_key` itself.

    The record is the entity's creation: `start_s` is when it was created.
    `end_s` is when it ended. Storing an Observation of it whose
    `observation_type` is "start" or "end" sets `start_s` or `end_s` to that
    observation's time.

    `start_observation` is a StartObservation that holds the entity's
    values at its `start_s`, e.g. `start_observation = Departure`. The entity
    completes it into an Observation of itself when it is defined."""

    start_s: float | timedelta | None
    end_s: float | timedelta | None = None
    start_observation: ClassVar[type[Record] | None] = None

    def __post_init__(self):
        parent_post_init = getattr(super(), "__post_init__", None)
        if parent_post_init is not None:
            parent_post_init()
        self._validate_start_end()

    def _validate_start_end(self):
        name = type(self).__name__
        for attr in ("start_s", "end_s"):
            time = getattr(self, attr)
            if time is None:
                continue
            if not isinstance(time, timedelta) and (
                isinstance(time, bool) or not isinstance(time, Real)
            ):
                raise TypeError(
                    f"{name}.{attr} must be a number of seconds, a timedelta "
                    f"or None; got {type(time).__name__!r}"
                )
            if isinstance(time, timedelta):
                time = time.total_seconds()
            if time < 0:
                raise ValueError(f"{name}.{attr} must be non-negative")
            setattr(self, attr, float(time))
        if (
            self.start_s is not None
            and self.end_s is not None
            and self.end_s < self.start_s
        ):
            raise ValueError(
                f"{name}.end_s must not be before start_s; "
                f"got start_s={self.start_s}, end_s={self.end_s}"
            )

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        if not abstract and "primary_key" not in cls.__dict__:
            cls.primary_key = cls.id_field
        super().__init_subclass__(abstract=abstract, **kwargs)
        if not abstract and "start_observation" in cls.__dict__:
            cls._complete_start_observation()

    @classmethod
    def _complete_start_observation(cls):
        # Checks for _complete rather than the StartObservation class, which
        # would have to be imported from observation.py, which imports this
        # module.
        complete = getattr(cls.start_observation, "_complete", None)
        if complete is None:
            raise TypeError(
                f"{cls.__name__}.start_observation must be a "
                f"StartObservation subclass; got {cls.start_observation!r}"
            )
        complete(cls)
