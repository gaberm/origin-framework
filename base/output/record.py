from typing import ClassVar, Literal


class Record:
    table_name: ClassVar[str]
    primary_key: ClassVar[tuple[str, ...]]
    indexed: ClassVar[tuple[str, ...]] = ()
    on_conflict: ClassVar[Literal["ignore", "update"]] = "ignore"
    diagnostic: ClassVar[bool] = False
    time_field: ClassVar[str] = "time"

    def __post_init__(self):
        for name in dir(self):
            if name.startswith("_validate_"):
                getattr(self, name)()

    def _validate_primary_key(self):
        for key in self.primary_key:
            if not hasattr(self, key):
                raise ValueError(f"Primary key field '{key}' is missing.")

    def _validate_indexed(self):
        for key in self.indexed:
            if not hasattr(self, key):
                raise ValueError(f"Indexed field '{key}' is missing.")
