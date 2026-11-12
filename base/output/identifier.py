from dataclasses import dataclass
from typing import ClassVar
from base.output.abstractable import Abstractable
from base.utils import declare_field


@dataclass(kw_only=True)
class Identifier(Abstractable):
    """Base class for records that identify something.

    `id_field` names the field that holds the id, or several fields when the
    id is composite, e.g. `("port_id", "station_id")`. The default is "id".
    Concrete subclasses get it as a tuple. Every id field holds a string.
    """

    id_field: ClassVar[str | tuple[str, ...]] = "id"

    def __post_init__(self):
        super().__post_init__()
        self._validate_id()

    def _validate_id(self):
        for name in self.id_field:
            id_value = getattr(self, name)
            if not isinstance(id_value, str):
                raise TypeError(
                    f"{type(self).__name__}.{name} must be a string; "
                    f"got {type(id_value).__name__!r}"
                )

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        if not abstract:
            cls._normalize_id_field()
            for name in cls.id_field:
                declare_field(cls, name, str)
        super().__init_subclass__(abstract=abstract, **kwargs)

    @classmethod
    def _normalize_id_field(cls):
        value = cls.id_field
        cls.id_field = (value,) if isinstance(value, str) else tuple(value)
        if not cls.id_field:
            raise ValueError(f"{cls.__name__} must declare an id_field")
