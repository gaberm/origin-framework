from typing import ClassVar, Literal
from base.output.timestamp import Timestamp


class Record:
    """Base class for model outputs; each subclass is stored in its own table.

    `table_name` is the name of that table.

    `primary_key` names the fields that identify a record. It is required.

    `indexed` names the fields to index. A Timestamp's `time_field` is
    always indexed.

    `on_conflict` says what happens to a record whose primary key already
    exists: "IGNORE" (the default) keeps the stored one, "UPDATE" replaces it.

    `primary_key` and `indexed` fields must be declared on the record.
    """

    table_name: ClassVar[str]
    primary_key: ClassVar[str | tuple[str, ...]]
    indexed: ClassVar[str | tuple[str, ...]] = ()
    on_conflict: ClassVar[Literal["IGNORE", "UPDATE"]] = "IGNORE"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._validate_primary_key_and_indexed()
        cls._index_time_field()
        cls._validate_on_conflict()

    @classmethod
    def _validate_primary_key_and_indexed(cls):
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
        if not cls.primary_key:
            raise ValueError(f"{cls.__name__} must declare a primary_key")

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
