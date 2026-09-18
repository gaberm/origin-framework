from dataclasses import dataclass
from datetime import timedelta
from numbers import Real
from typing import ClassVar


@dataclass(kw_only=True)
class Timestamp:
    time_s: float | int | timedelta
    time_field: ClassVar[str] = "time_s"

    def __post_init__(self):
        parent_post_init = getattr(super(), "__post_init__", None)
        if parent_post_init is not None:
            parent_post_init()
        if not isinstance(self.time_s, timedelta) and (
            isinstance(self.time_s, bool) or not isinstance(self.time_s, Real)
        ):
            raise TypeError(
                f"{type(self).__name__}.{self.time_field} must be a number of "
                f"seconds or a timedelta; got {type(self.time_s).__name__!r}"
            )
        if isinstance(self.time_s, timedelta):
            self.time_s = self.time_s.total_seconds()
        if self.time_s < 0:
            raise ValueError(
                f"{type(self).__name__}.{self.time_field} must be non-negative"
            )
        if isinstance(self.time_s, int):
            self.time_s = float(self.time_s)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        all_annotations = {
            name
            for parent in cls.__mro__
            for name in getattr(parent, "__annotations__", {})
        }
        if cls.time_field not in all_annotations:
            raise TypeError(f"time_field must be a field in {cls.__name__}")
