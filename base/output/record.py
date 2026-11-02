from dataclasses import dataclass
from typing import ClassVar, Literal
from base.output.abstractable import Abstractable
from base.output.timestamp import Timestamp


@dataclass(kw_only=True)
class Record(Abstractable):
    """Base class for model outputs; each subclass is stored in its own table.

    `table_name` is the name of that table. It defaults to the class name

    `primary_key` names the fields that identify a record. It is required.

    `indexed` names the fields to index. A Timestamp's `time_field` is
    always indexed.

    `on_conflict` says what happens to a record whose primary key already
    exists: "ignore" (the default) keeps the stored one, "update" replaces it.

    `diagnostic` marks records that are only for analysis; the state memory
    writes them in the background instead of before the next step. The
    default is False.

    `primary_key` and `indexed` fields must be declared on the record.

    A subclass declared with `abstract=True` is a base for other records and
    has no table; none of the above is checked for it.
    """

    table_name: ClassVar[str]
    primary_key: ClassVar[str | tuple[str, ...]]
    indexed: ClassVar[str | tuple[str, ...]] = ()
    on_conflict: ClassVar[Literal["ignore", "update"]] = "ignore"
    diagnostic: ClassVar[bool] = False

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        super().__init_subclass__(abstract=abstract, **kwargs)
        if abstract:
            return
        cls._default_table_name()
        cls._validate_primary_key_and_indexed()
        cls._index_time_field()
        cls._validate_on_conflict()
        cls._validate_diagnostic()

    @classmethod
    def _default_table_name(cls):
        if "table_name" not in cls.__dict__:
            cls.table_name = cls.__name__

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
        if cls.on_conflict not in ("ignore", "update"):
            raise ValueError(
                f"{cls.__name__}.on_conflict must be 'ignore' or 'update'; got {cls.on_conflict!r}"
            )

    @classmethod
    def _validate_diagnostic(cls):
        if not isinstance(cls.diagnostic, bool):
            raise TypeError(
                f"{cls.__name__}.diagnostic must be a boolean; got {cls.diagnostic!r}"
            )
