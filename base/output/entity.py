import sys
import types
from dataclasses import dataclass, field, fields
from datetime import timedelta
from functools import cache
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints
from base.output.identifier import Identifier
from base.output.record import Record
from base.utils import to_seconds


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
        raise TypeError(
            f"{context}: ref() must name an Entity subclass; got {target!r}"
        )
    if len(target.id_field) != 1:
        raise ValueError(
            f"{context}: {target.__name__} has the composite id {target.id_field}; "
            f"ref() holds one id"
        )
    if tuple(target.primary_key) != target.id_field:
        raise ValueError(
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
    """Base class for records that identify something across a run.

    `id_field` names the field, or fields, that identify the entity. It is the
    primary key unless the class sets `primary_key`.

    `start_s` is when the entity was created and `end_s` when it ended. Storing
    an Observation of it whose `observation_type` is "start" or "end" sets
    `start_s` or `end_s` to that observation's time.

    `start_observation` is a StartObservation holding the entity's values at
    `start_s`, e.g. `start_observation = Departure`. The entity completes it
    into an Observation of itself when the class is defined.

    `initialize_only` marks entities a model returns only from `initialize`,
    e.g. infrastructure that exists for the whole run. They are stored before
    the first step, so reading them never makes a model wait for the one
    producing them. They have no `start_s` or `end_s`. The default is False.
    """

    start_s: float | timedelta | None
    end_s: float | timedelta | None = None
    start_observation: ClassVar[type[Record] | None] = None
    initialize_only: ClassVar[bool] = False

    def __post_init__(self):
        super().__post_init__()
        self._normalize_start_end()
        self._validate_start_end()
        self._validate_initialize_only_times()

    def _normalize_start_end(self):
        for attr in ("start_s", "end_s"):
            time = getattr(self, attr)
            if time is not None:
                name = f"{type(self).__name__}.{attr}"
                setattr(self, attr, to_seconds(time, name))

    def _validate_start_end(self):
        if (
            self.start_s is not None
            and self.end_s is not None
            and self.end_s < self.start_s
        ):
            raise ValueError(
                f"{type(self).__name__}.end_s must not be before start_s; "
                f"got start_s={self.start_s}, end_s={self.end_s}"
            )

    def _validate_initialize_only_times(self):
        # Stored at initialize, before there is a simulation time to have
        # been created or ended at.
        if not self.initialize_only:
            return
        for attr in ("start_s", "end_s"):
            if getattr(self, attr) is not None:
                raise ValueError(
                    f"{type(self).__name__} is initialize_only, so it exists "
                    f"for the whole run and {attr} must be None; "
                    f"got {getattr(self, attr)}"
                )

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        if not abstract and "primary_key" not in cls.__dict__:
            cls.primary_key = cls.id_field
        super().__init_subclass__(abstract=abstract, **kwargs)
        if abstract:
            return
        cls._validate_initialize_only()
        if "start_observation" in cls.__dict__:
            cls._complete_start_observation()

    @classmethod
    def _validate_initialize_only(cls):
        if not isinstance(cls.initialize_only, bool):
            raise TypeError(
                f"{cls.__name__}.initialize_only must be a boolean; "
                f"got {cls.initialize_only!r}"
            )

    @classmethod
    def _complete_start_observation(cls):
        # Imported here because observation.py imports this module.
        from base.output.observation import StartObservation

        start_observation = cls.start_observation
        if not (
            isinstance(start_observation, type)
            and issubclass(start_observation, StartObservation)
        ):
            raise TypeError(
                f"{cls.__name__}.start_observation must be a "
                f"StartObservation subclass; got {start_observation!r}"
            )
        start_observation.complete(cls)
