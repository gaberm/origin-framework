import re
from datetime import timedelta
from numbers import Real
from typing import Any


def as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def to_seconds(time: float | timedelta, label: str) -> float:
    """Checks that a time is non-negative and converts a timedelta to seconds."""
    if isinstance(time, timedelta):
        time = time.total_seconds()
    elif isinstance(time, bool) or not isinstance(time, Real):
        raise TypeError(
            f"{label} must be a number of seconds or a timedelta; "
            f"got {type(time).__name__!r}"
        )
    if time < 0:
        raise ValueError(f"{label} must be non-negative")
    return float(time)


def declared_field_names(cls: type) -> set[str]:
    """The names annotated on `cls` or any of its parents."""
    return {
        name
        for parent in cls.__mro__
        for name in parent.__dict__.get("__annotations__", {})
    }


def declare_field(cls: type, name: str, field_type: Any) -> None:
    """Annotate `name` on `cls`, so that @dataclass makes it a field, unless
    `cls` or a parent already declares it."""
    if name in declared_field_names(cls):
        return
    if "__annotations__" not in cls.__dict__:
        cls.__annotations__ = {}
    cls.__annotations__[name] = field_type


def validate_run_id(run_id: str) -> None:
    """Checks that a run id is a valid PostgreSQL schema name, since it names the run's schema."""
    if not isinstance(run_id, str):
        raise TypeError(f"run_id must be a str; got {type(run_id).__name__!r}")
    if not re.match(r"^[a-z_][a-z0-9_]{0,62}$", run_id):
        raise ValueError(
            f"run_id '{run_id}' is not a valid PostgreSQL schema name. "
            f"Use lowercase letters, digits, and underscores only, "
            f"start with a letter or underscore, and keep it to at most 63 characters."
        )
