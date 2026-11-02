from dataclasses import dataclass
from typing import ClassVar, Literal, Type, get_args
from base.output.entity import Entity
from base.output.identifier import Identifier
from base.output.record import Record
from base.output.timestamp import Timestamp
from base.utils import as_list

OBSERVATION_TYPE = Literal["start", "update", "end"]


@dataclass(kw_only=True)
class Observation(Record, Identifier, Timestamp, abstract=True):
    """A timestamped record of an entity, identified by its `id_field` and
    `time_field` together, which are its primary key unless the class sets
    `primary_key` itself.

    `entity` is the Entity class the observation is about. It is required,
    and the observation's `id_field` must be the same as the entity's.

    `observation_type` is "start", "update" (the default) or "end". Storing
    a "start" or "end" observation sets the entity's `start_s` or `end_s` to
    its time."""

    entity: ClassVar[Type[Entity]]
    observation_type: ClassVar[OBSERVATION_TYPE] = "update"

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        if not abstract and "primary_key" not in cls.__dict__:
            cls.primary_key = (*as_list(cls.id_field), cls.time_field)
        super().__init_subclass__(abstract=abstract, **kwargs)
        if abstract:
            return
        cls._validate_entity()
        cls._validate_observation_type()

    @classmethod
    def _validate_entity(cls):
        entity = getattr(cls, "entity", None)
        if entity is None:
            raise ValueError(f"{cls.__name__} must declare an entity")
        if not (
            isinstance(entity, type)
            and issubclass(entity, Entity)
            and entity is not Entity
        ):
            raise TypeError(
                f"{cls.__name__}.entity must be an Entity subclass; got {entity!r}"
            )
        if cls.id_field != entity.id_field:
            raise ValueError(
                f"{cls.__name__}.id_field must match {entity.__name__}.id_field "
                f"{entity.id_field}; got {cls.id_field}"
            )

    @classmethod
    def _validate_observation_type(cls):
        if cls.observation_type not in get_args(OBSERVATION_TYPE):
            raise ValueError(
                f"{cls.__name__}.observation_type must be one of "
                f"{get_args(OBSERVATION_TYPE)}; got {cls.observation_type!r}"
            )


@dataclass(kw_only=True)
class StartObservation(Observation, abstract=True):
    """An entity's values at its `start`, in its own table. Declared without
    `@dataclass` and completed by the entity that names it:

        class Departure(StartObservation):
            charge_wh: float

        @dataclass(kw_only=True)
        class Trip(Entity):
            start_observation = Departure
            ...

    The entity sets `entity` and `id_field`; the usual Observation setup then
    adds the id and time fields and the primary key. Its `observation_type`
    is "start".
    """

    observation_type: ClassVar[OBSERVATION_TYPE] = "start"

    def __init_subclass__(cls, **kwargs):
        # Set up only once an entity names it, in _complete.
        super().__init_subclass__(abstract=True, **kwargs)

    @classmethod
    def _complete(cls, entity: Type[Entity]):
        if "entity" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} is already the start observation of "
                f"{cls.entity.__name__}"
            )
        if "__dataclass_fields__" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} must be declared without @dataclass; "
                f"{entity.__name__} makes it a dataclass"
            )
        cls.entity = entity
        cls.id_field = entity.id_field
        # The setup its own __init_subclass__ skipped.
        Observation.__dict__["__init_subclass__"].__func__(cls)
        dataclass(kw_only=True)(cls)
