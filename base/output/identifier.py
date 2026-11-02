from dataclasses import dataclass
from typing import ClassVar
from base.output.abstractable import Abstractable


@dataclass(kw_only=True)
class Identifier(Abstractable):
    """Base class for records that identify something.

    `id_field` names the field that holds the id, or several fields when the
    id is composite, e.g. `("port_id", "station_id")`. The default is "id".
    Concrete subclasses get it as a tuple. Every id field holds a string.
    """

    id_field: ClassVar[str | tuple[str, ...]] = "id"

    def __post_init__(self):
        parent_post_init = getattr(super(), "__post_init__", None)
        if parent_post_init is not None:
            parent_post_init()
        self._validate_id()

    def _validate_id(self):
        for name in self.id_field:
            id = getattr(self, name)
            if not isinstance(id, str):
                raise TypeError(
                    f"{type(self).__name__}.{name} must be a string; "
                    f"got {type(id).__name__!r}"
                )

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        if not abstract:
            cls._normalize_id_field()
            cls._declare_id_field()
        super().__init_subclass__(abstract=abstract, **kwargs)

    @classmethod
    def _normalize_id_field(cls):
        value = cls.id_field
        cls.id_field = (value,) if isinstance(value, str) else tuple(value)
        if not cls.id_field:
            raise ValueError(f"{cls.__name__} must declare an id_field")

    @classmethod
    def _declare_id_field(cls):
        declared = {
            name
            for parent in cls.__mro__
            for name in parent.__dict__.get("__annotations__", {})
        }
        if "__annotations__" not in cls.__dict__:
            cls.__annotations__ = {}
        for name in cls.id_field:
            if name not in declared:
                cls.__annotations__[name] = str
