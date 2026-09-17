from typing import ClassVar, Literal
from base.output.timestamp import Timestamp


class Record:
    table_name: ClassVar[str]
    primary_key: ClassVar[str | tuple[str, ...]]
    indexed: ClassVar[str | tuple[str, ...]] = ()
    on_conflict: ClassVar[Literal["IGNORE", "UPDATE"]] = "IGNORE"
    diagnostic: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._validate_primary_key_indexed()
        cls._index_time_field()
        cls._validate_on_conflict()
        cls._validate_diagnostic()

    @classmethod
    def _validate_primary_key_indexed(cls):
        field_names = {
            name
            for parent in cls.__mro__
            for name in getattr(parent, "__annotations__", {})
        }
        for attr in ("primary_key", "indexed"):
            value = getattr(cls, attr, ())
            value = (value,) if isinstance(value, str) else tuple(value)
            setattr(cls, attr, value)
            for key in value:
                if key not in field_names:
                    raise ValueError(
                        f"{cls.__name__}: {attr} field '{key}' is not declared"
                    )

    @classmethod
    def _index_time_field(cls):
        if issubclass(cls, Timestamp):
            cls.indexed = tuple(dict.fromkeys((cls.time_field, *cls.indexed)))

    @classmethod
    def _validate_on_conflict(cls):
        if cls.on_conflict not in ("IGNORE", "UPDATE"):
            raise ValueError(
                f"{cls.__name__}.on_conflict must be 'IGNORE' or 'UPDATE'; got {cls.on_conflict!r}"
            )

    @classmethod
    def _validate_diagnostic(cls):
        if not isinstance(cls.diagnostic, bool):
            raise TypeError(
                f"{cls.__name__}.diagnostic must be a boolean; got {cls.diagnostic!r}"
            )
